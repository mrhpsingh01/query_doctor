from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import psycopg

from querydoctor.db.errors import ExplainFailed, ExplainTimeout, ParamMismatch
from querydoctor.db.params import check_params  # noqa: F401  (re-exported for callers)
from querydoctor.db.protocol import execute_single

PLAN_ONLY = "FORMAT JSON, VERBOSE, COSTS, SETTINGS"
WITH_ACTUALS = "ANALYZE, BUFFERS, WAL, FORMAT JSON, VERBOSE, SETTINGS"


def explain(conn: psycopg.Connection, sql: str, params: Sequence[Any] = (), *, execute: bool = False) -> list[dict]:
    """Return PostgreSQL's raw JSON plan for ``sql`` ($1..$n placeholders) with ``params`` bound.

    The statement goes through the extended protocol (see ``db.protocol``): PostgreSQL itself enforces a single
    statement and the values are bound as parameters, never interpolated. With parameters known at plan time the
    planner uses a custom plan, so the plan reflects the real values.

    ``execute=False`` is planner-only. ``execute=True`` runs the query (EXPLAIN ANALYZE) and must only be called for
    requests the user explicitly approved. The transaction is always rolled back, so nothing persists.
    """
    options = WITH_ACTUALS if execute else PLAN_ONLY
    try:
        result = execute_single(conn, f"EXPLAIN ({options}) {sql}", params)
        return json.loads(result.get_value(0, 0))
    except psycopg.errors.QueryCanceled as exc:
        raise ExplainTimeout("statement exceeded the session timeout") from exc
    except (psycopg.errors.InvalidTextRepresentation, psycopg.errors.DatatypeMismatch, psycopg.errors.InvalidDatetimeFormat) as exc:
        raise ParamMismatch(f"a parameter does not fit its type: {exc.diag.message_primary}") from exc
    except psycopg.Error as exc:
        raise ExplainFailed(exc.diag.message_primary or str(exc), exc.sqlstate) from exc
    finally:
        _cleanup(conn)


def _cleanup(conn: psycopg.Connection) -> None:
    try:
        conn.rollback()
    except psycopg.Error:
        pass
