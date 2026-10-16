"""MongoDB workload: the profiler's ``system.profile`` grouped by query shape.

With the profiler at level 2 every operation is recorded. A *shape* is the logged ``find`` or ``aggregate`` with its literals
replaced by ``?`` (the same normalization the validator uses, so a shape's fingerprint equals the fingerprint of an analysis of
the same query). Per shape: calls, total/mean/standard deviation of the server-reported time, documents and keys examined.

Mapping onto the shared workload columns (so the same dashboard works): ``shared_read_blocks`` holds documents examined and
``shared_hit_blocks`` holds index keys examined, the closest MongoDB counterparts of I/O work.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from bson import json_util
from pymongo.database import Database

from querydoctor.mongo.validator import MongoValidationError, parametrized_spec, validate_mongo
from querydoctor.workload.models import WorkloadQuery
from querydoctor.workload.pgss import Z95

FIND_KEYS = ("find", "filter", "sort", "projection", "limit", "skip")
AGGREGATE_KEYS = ("aggregate", "pipeline")
DEFAULT_LIMIT = 5_000


def _spec(command: dict[str, Any]) -> dict[str, Any] | None:
    """The query part of a logged command, or None if it is not a find or an aggregate."""
    if "explain" in command:
        return None
    if "find" in command:
        keys = FIND_KEYS
    elif "aggregate" in command:
        keys = AGGREGATE_KEYS
    else:
        return None
    spec = {k: command[k] for k in keys if k in command}
    if not isinstance(spec.get("find", spec.get("aggregate")), str):
        return None
    return spec


def read_profile(db: Database, limit: int = DEFAULT_LIMIT) -> tuple[list[WorkloadQuery], int]:
    """``(queries by total time, number of ignored profile entries)`` for the database's recent operations."""
    entries = db["system.profile"].find({"op": {"$in": ["query", "command"]}}).sort("ts", -1).limit(limit)
    return parse_profile(entries)


def parse_profile(entries) -> tuple[list[WorkloadQuery], int]:
    """Group an explicitly captured profiler sample; never retains command literals."""
    groups: dict[str, dict[str, Any]] = {}
    times: dict[str, list[float]] = defaultdict(list)
    skipped = 0
    for entry in entries:
        spec = _spec(entry.get("command") or {})
        if spec is None or str(entry.get("ns", "")).split(".", 1)[-1].startswith("system."):
            skipped += 1
            continue
        try:
            vq = validate_mongo(json_util.dumps(spec), [])
        except (MongoValidationError, ValueError):
            skipped += 1
            continue
        try:
            millis = float(entry.get("millis", 0))
            docs = int(entry.get("docsExamined", 0) or 0)
            keys = int(entry.get("keysExamined", 0) or 0)
            rows = int(entry.get("nreturned", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            skipped += 1
            continue
        if not math.isfinite(millis) or millis < 0 or min(docs, keys, rows) < 0:
            skipped += 1
            continue
        group = groups.setdefault(
            vq.fingerprint,
            {"vq": vq, "docs": 0, "keys": 0, "rows": 0, "sample": parametrized_spec(vq.query)},  # never the logged values
        )
        times[vq.fingerprint].append(millis)
        group["docs"] += docs
        group["keys"] += keys
        group["rows"] += rows
    out: list[WorkloadQuery] = []
    for fingerprint, g in groups.items():
        values = times[fingerprint]
        calls, total = len(values), sum(values)
        mean = total / calls
        std = math.sqrt(sum((v - mean) ** 2 for v in values) / calls)
        out.append(
            WorkloadQuery(
                fingerprint=fingerprint, normalized_sql=g["vq"].shape, sample_sql=g["sample"], calls=calls, total_ms=total, mean_ms=mean,
                stddev_ms=std, min_ms=min(values), max_ms=max(values), rows=g["rows"], shared_hit_blocks=g["keys"],
                shared_read_blocks=g["docs"], temp_blocks=0, p95_est_ms=mean + Z95 * std,
                p95_observed_ms=sorted(values)[math.ceil(0.95 * calls) - 1], tables=g["vq"].collections,
            )
        )
    out.sort(key=lambda q: q.total_ms, reverse=True)
    return out, skipped
