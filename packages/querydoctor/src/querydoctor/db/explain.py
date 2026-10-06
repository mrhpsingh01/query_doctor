from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import psycopg

from querydoctor.db.errors import ExplainFailed, ExplainTimeout, ParamMismatch
from querydoctor.db.params import check_params, literal_args

PLAN_ONLY = "FORMAT JSON, VERBOSE, COSTS, SETTINGS"
WITH_ACTUALS = "ANALYZE, BUFFERS, WAL, FORMAT JSON, VERBOSE, SETTINGS"
_STMT = "qd_stmt"


def explain(conn: psycopg.Connection, sql: str, params: Sequence[Any] = (), *, execute: bool = False) -> list[dict]:
    """Return PostgreSQL's raw JSON plan for ``sql`` ($1..$n placeholders) with ``params`` applied.

    The query is PREPAREd as written (so ``%`` and ``$n`` in it are never reinterpreted) and run through
    ``EXPLAIN ... EXECUTE`` with ``plan_cache_mode = force_custom_plan``, so the plan reflects the actual values.
    EXECUTE arguments cannot be bound as protocol parameters, so values are rendered with psycopg's literal quoting
    (``sql.Literal``) after a type whitelist - never string-formatted by hand.

    ``execute=False`` is planner-only. ``execute=True`` runs the query (EXPLAIN ANALYZE) and must only be called for
    requests the user explicitly approved. The transaction is always rolled back, so nothing persists.
    """
    check_params(params)
    options = WITH_ACTUALS if execute else PLAN_ONLY
    try:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL plan_cache_mode = force_custom_plan")
            cur.execute(f"PREPARE {_STMT} AS {sql}")  # no params -> psycopg does not touch '%' in the SQL
            expected = cur.execute(
                "SELECT cardinality(parameter_types) FROM pg_prepared_statements WHERE name = %s", (_STMT,)
            ).fetchone()[0]
            if expected != len(params):
                raise ParamMismatch(f"query expects {expected} parameter(s) but {len(params)} were supplied")
            args = literal_args(conn, params)
            row = cur.execute(f"EXPLAIN ({options}) EXECUTE {_STMT}{args}").fetchone()
            return row[0]
    except psycopg.errors.QueryCanceled as exc:
        raise ExplainTimeout("statement exceeded the session timeout") from exc
    except (psycopg.errors.InvalidTextRepresentation, psycopg.errors.DatatypeMismatch, psycopg.errors.InvalidDatetimeFormat) as exc:
        raise ParamMismatch(f"a parameter does not fit its type: {exc.diag.message_primary}") from exc
    except psycopg.Error as exc:
        raise ExplainFailed(exc.diag.message_primary or str(exc), exc.sqlstate) from exc
    finally:
        _cleanup(conn)


def _cleanup(conn: psycopg.Connection) -> None:
    """Roll back and drop the session-level prepared statement (PREPARE is not transactional)."""
    try:
        conn.rollback()
        conn.execute("DEALLOCATE ALL")
        conn.rollback()
    except psycopg.Error:
        pass
