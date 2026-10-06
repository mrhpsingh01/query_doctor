"""The benchmark protocol for MongoDB, run inside a throwaway database.

    sandbox copy -> warm up + measure the original query -> create the REAL index -> warm up + measure again
                 -> compare medians, variance and result checksums -> drop the sandbox (always)

Timings are the server-reported ``executionTimeMillis`` of ``explain("executionStats")``. The result checksum is computed in
the database (a document count and a sum of hashed documents), so no document ever leaves it and row order is irrelevant.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from typing import Any

from pymongo import MongoClient
from pymongo.errors import PyMongoError

from querydoctor.benchmark.models import BenchmarkConfig, BenchmarkResult, RunSample
from querydoctor.benchmark.reliability import compare_outputs, reliability_warnings
from querydoctor.benchmark.runner import BenchmarkBusy, BenchmarkCancelled, BenchmarkFailed, BenchmarkTimeout
from querydoctor.benchmark.stats import improvement_pct, summarize
from querydoctor.candidates.models import IndexCandidate
from querydoctor.db.errors import DbError
from querydoctor.mongo.candidates import build_create_index
from querydoctor.mongo.explain import explain_query
from querydoctor.mongo.plan import normalize_plan
from querydoctor.mongo.query import ValidatedMongoQuery
from querydoctor.mongo.sandbox import MongoSandbox, SandboxError, acquire_slot
from querydoctor.plan_parser import iter_nodes

log = logging.getLogger(__name__)
HASH_MODULUS = 1_000_003


def checksum_pipeline(vq: ValidatedMongoQuery) -> tuple[str, list[dict[str, Any]]]:
    """(collection, pipeline) computing ``{n, h}``: the number of result documents and an order-insensitive hash of them."""
    q = vq.query
    if q.kind == "find":
        stages: list[dict[str, Any]] = [{"$match": q.filter}]
        if q.sort:
            stages.append({"$sort": q.sort})
        if q.skip:
            stages.append({"$skip": q.skip})
        if q.limit:
            stages.append({"$limit": q.limit})
        if q.projection:
            stages.append({"$project": q.projection})
    else:
        stages = list(q.pipeline or [])
    stages.append({"$group": {"_id": None, "n": {"$sum": 1}, "h": {"$sum": {"$mod": [{"$toHashedIndexKey": "$$ROOT"}, HASH_MODULUS]}}}})
    return q.collection, stages


def result_checksum(db, vq: ValidatedMongoQuery) -> str:
    collection, pipeline = checksum_pipeline(vq)
    rows = list(db[collection].aggregate(pipeline, maxTimeMS=60_000, allowDiskUse=True))
    n, h = (rows[0]["n"], rows[0]["h"]) if rows else (0, 0)
    return f"{n}:{h}"


def _sample(plan) -> RunSample:
    rows = plan.root.actual_rows
    return RunSample(execution_ms=float(plan.execution_time_ms or 0.0), rows=int(rows) if rows is not None else None)


def _runs(db, vq, params, n, warmups, check, on_progress, warm_stage, measure_stage):
    on_progress(warm_stage)
    plan = None
    for _ in range(warmups):
        check()
        explain_query(db, vq, params, execute=True)
    on_progress(measure_stage)
    samples: list[RunSample] = []
    for _ in range(n):
        check()
        plan = normalize_plan(explain_query(db, vq, params, execute=True))
        samples.append(_sample(plan))
    return samples, plan


def run_mongo_benchmark(
    bench_uri: str,
    vq: ValidatedMongoQuery,
    params: Sequence[Any],
    candidate: IndexCandidate,
    config: BenchmarkConfig,
    on_progress: Callable[[str], None] = lambda stage: None,
    *,
    cancelled: Callable[[], bool] = lambda: False,
    source_db: str = "qd_sample",
) -> BenchmarkResult:
    try:
        keys = build_create_index(candidate)
    except ValueError as exc:
        raise BenchmarkFailed("The index definition is not usable, so it was not run.") from exc
    if candidate.table not in vq.collections:
        raise BenchmarkFailed("The index targets a collection the query does not use, so it was not run.")
    deadline = time.monotonic() + config.timeout_s

    def check() -> None:
        if cancelled():
            raise BenchmarkCancelled("The benchmark was cancelled.")
        if time.monotonic() >= deadline:
            raise BenchmarkTimeout(f"The benchmark did not finish within {config.timeout_s} seconds.")

    client: MongoClient = MongoClient(bench_uri, serverSelectionTimeoutMS=5000)
    lease = None
    sandbox = MongoSandbox(client, source_db)
    try:
        lease = acquire_slot(client)
        on_progress("creating_sandbox")
        sandbox.create(vq.collections)
        db = sandbox.db
        version = client.server_info().get("version", "?")
        counts = {c: db[c].estimated_document_count() for c in vq.collections}
        snapshot = hashlib.sha1(f"{source_db}:{sorted(counts.items())}".encode()).hexdigest()[:12]

        before_samples, before_plan = _runs(db, vq, params, config.runs, config.warmups, check, on_progress, "warmup_before", "measuring_before")
        check_before = result_checksum(db, vq)

        check()
        on_progress("creating_index")
        started = time.perf_counter()
        index_name = db[candidate.table].create_index(keys)
        build_ms = (time.perf_counter() - started) * 1000
        size = int(db.command("collStats", candidate.table)["indexSizes"].get(index_name, 0))

        check()
        on_progress("analyzing")  # MongoDB keeps its own statistics: nothing to refresh
        after_samples, after_plan = _runs(db, vq, params, config.runs, config.warmups, check, on_progress, "warmup_after", "measuring_after")
        check_after = result_checksum(db, vq)

        on_progress("comparing")
        before = summarize([s.execution_ms for s in before_samples])
        after = summarize([s.execution_ms for s in after_samples])
        first_ratio = max(
            before_samples[0].execution_ms / before.median_ms if before.median_ms else 1.0,
            after_samples[0].execution_ms / after.median_ms if after.median_ms else 1.0,
        )
        match, notes = compare_outputs(check_before, check_after, None, None)
        index_used = any(n.index_name == index_name for n in iter_nodes(after_plan.root))
        warnings = reliability_warnings(before, after, config=config, checksum_match=match, first_run_ratio=first_ratio) + notes
        if not index_used:
            warnings.append("The new index was not used by the query's final plan, so any speed difference is not attributable to it.")
        return BenchmarkResult(
            before=before, after=after, before_samples=before_samples, after_samples=after_samples,
            improvement_pct=improvement_pct(before.median_ms, after.median_ms), index_size_bytes=size, index_build_ms=build_ms,
            plan_fingerprint_before=before_plan.fingerprint, plan_fingerprint_after=after_plan.fingerprint, checksum_match=match,
            index_used=index_used, warnings=warnings, pg_version=f"MongoDB {version}", settings={"engine": "mongodb"},
            dataset_snapshot=snapshot, ddl=candidate.ddl,
        )
    except (BenchmarkFailed, BenchmarkBusy):
        raise
    except SandboxError as exc:
        raise BenchmarkFailed(f"The benchmark copy could not be prepared ({exc}). Try again in a moment.") from exc
    except DbError as exc:
        raise BenchmarkFailed(f"The benchmark failed: {exc}") from exc
    except PyMongoError as exc:
        raise BenchmarkFailed("The benchmark failed: the MongoDB server rejected or lost a command.") from exc
    finally:
        # Order matters: dropping the sandbox and releasing the slot must never depend on progress reporting working.
        with suppress(Exception):
            sandbox.drop()
        if lease is not None:
            with suppress(Exception):
                lease.release()
        with suppress(Exception):
            client.close()
        with suppress(Exception):
            on_progress("teardown")
