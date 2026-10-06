"""Ask MongoDB for the plan of a validated query.

``execute=False`` is the ``queryPlanner`` verbosity: the plan without running the query. ``execute=True`` is
``executionStats``, which runs the query (the MongoDB counterpart of EXPLAIN ANALYZE) and so is only used for requests the
person explicitly approved. Every command carries ``maxTimeMS``.
"""

from __future__ import annotations

from typing import Any

from pymongo.database import Database
from pymongo.errors import ExecutionTimeout, OperationFailure, PyMongoError

from querydoctor.db.errors import ExplainFailed, ExplainTimeout
from querydoctor.mongo.query import ValidatedMongoQuery

MAX_TIME_MS = 10_000


def build_command(vq: ValidatedMongoQuery, max_time_ms: int = MAX_TIME_MS) -> dict[str, Any]:
    """The server command for the query, built from structure (never from a string)."""
    q = vq.query
    if q.kind == "find":
        cmd: dict[str, Any] = {"find": q.collection, "filter": q.filter}
        for key in ("sort", "projection", "limit", "skip"):
            value = getattr(q, key)
            if value is not None:
                cmd[key] = value
    else:
        cmd = {"aggregate": q.collection, "pipeline": q.pipeline, "cursor": {}}
    cmd["maxTimeMS"] = max_time_ms
    return cmd


def explain_query(db: Database, vq: ValidatedMongoQuery, params: Any = (), *, execute: bool, max_time_ms: int = MAX_TIME_MS) -> dict[str, Any]:
    """The raw explain document. ``params`` are already substituted in ``vq`` (kept for the adapter signature)."""
    verbosity = "executionStats" if execute else "queryPlanner"
    try:
        return db.command({"explain": build_command(vq, max_time_ms), "verbosity": verbosity, "maxTimeMS": max_time_ms})
    except ExecutionTimeout as exc:
        raise ExplainTimeout("the MongoDB command exceeded its time limit") from exc
    except OperationFailure as exc:
        raise ExplainFailed(str(exc.details.get("errmsg") if exc.details else exc)[:300], str(exc.code)) from exc
    except PyMongoError as exc:
        raise ExplainFailed("The MongoDB server could not be reached or the connection was lost.", None) from exc
