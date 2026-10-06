"""The MongoDB adapter: validation, explain and plan normalization, rules, ESR candidates and the sandbox benchmark."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from querydoctor.candidates.hypopg import ScoredCandidate
from querydoctor.mongo.benchmark import run_mongo_benchmark
from querydoctor.mongo.candidates import generate_mongo_candidates, prune_mongo_redundant
from querydoctor.mongo.explain import explain_query
from querydoctor.mongo.plan import normalize_plan
from querydoctor.mongo.rules import MongoContext, run_mongo_diagnosis
from querydoctor.mongo.schema import collect_mongo_schema
from querydoctor.mongo.validator import MAX_BYTES, validate_mongo
from querydoctor.mongo.workload import read_profile

NO_HYPOTHETICAL_NOTE = "MongoDB has no hypothetical indexes, so this stays a suggestion until a benchmark measures it."


class MongoAdapter:
    engine = "mongodb"

    def __init__(
        self,
        *,
        explain: Callable | None = None,
        collect_schema: Callable | None = None,
        benchmark_runner: Callable | None = None,
        bench_uri: str = "",
        source_db: str = "qd_sample",
    ) -> None:
        self._explain = explain or explain_query
        self._collect_schema = collect_schema or collect_mongo_schema
        self._benchmark = benchmark_runner or run_mongo_benchmark
        self._bench_uri, self._source_db = bench_uri, source_db

    def validate_query(self, text: str, params: Sequence[Any], *, max_chars: int):
        return validate_mongo(text, list(params), max_bytes=min(max_chars, MAX_BYTES))

    def collect_schema(self, conn, vq):
        return self._collect_schema(conn, vq.collections)

    def explain(self, conn, vq, params: Sequence[Any], *, execute: bool):
        return self._explain(conn, vq, params, execute=execute)

    def normalize_plan(self, raw):
        return normalize_plan(raw)

    def diagnose(self, plan, schema, vq, thresholds):
        return run_mongo_diagnosis(MongoContext(plan=plan, query=vq, collections=schema.collections))

    def generate_candidates(self, vq, schema, findings):
        return prune_mongo_redundant(generate_mongo_candidates(vq, schema.collections, list(findings)), schema.collections)

    def test_candidates(self, conn, vq, params: Sequence[Any], baseline, candidates: Sequence[Any], schema):
        out = []
        for c in candidates:
            c.rationale.append(NO_HYPOTHETICAL_NOTE)
            out.append(ScoredCandidate(c))  # untested: only a benchmark can raise the evidence level
        return out

    def benchmark(self, dsn: str, vq, params: Sequence[Any], candidate, config, *, on_progress, cancelled, template: str | None = None):
        return self._benchmark(
            self._bench_uri or dsn, vq, params, candidate, config, on_progress=on_progress, cancelled=cancelled, source_db=self._source_db
        )

    def collect_workload(self, conn, limit: int):
        return read_profile(conn, limit)
