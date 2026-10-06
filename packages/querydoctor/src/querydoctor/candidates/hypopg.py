"""Planner-only testing of index candidates with HypoPG: hypothetical indexes exist only in the session's memory,
cost nothing to build and are never written to the database."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import psycopg

from querydoctor.candidates.compare import PlanComparison, compare_plans
from querydoctor.candidates.models import IndexCandidate
from querydoctor.db import DbError, explain
from querydoctor.plan_parser import ParsedPlan, parse_explain

log = logging.getLogger(__name__)


@dataclass
class ScoredCandidate:
    candidate: IndexCandidate
    comparison: PlanComparison | None = None
    plan: ParsedPlan | None = None
    error: str | None = None


def _reset(conn: psycopg.Connection) -> None:
    try:
        conn.rollback()
        conn.execute("SELECT hypopg_reset()")
    finally:
        conn.rollback()


def score_candidates(
    conn: psycopg.Connection,
    sql: str,
    params: Sequence[Any],
    original: ParsedPlan,
    candidates: Sequence[IndexCandidate],
    table_rows: dict[str, int] | None = None,
) -> list[ScoredCandidate]:
    """For each candidate: create it as a hypothetical index, ask the planner (EXPLAIN without ANALYZE) for a plan,
    compare with ``original`` and reset. A candidate that cannot be tested yields an ``error`` instead of raising."""
    results: list[ScoredCandidate] = []
    for candidate in candidates:
        try:
            _reset(conn)
            conn.execute("SELECT * FROM hypopg_create_index(%s)", (candidate.ddl,)).fetchall()
            conn.rollback()  # hypothetical indexes live in session memory, so ending the transaction keeps them
            plan = parse_explain(explain(conn, sql, params, execute=False))
            results.append(ScoredCandidate(candidate, compare_plans(original, plan, candidate.ddl, table_rows), plan))
        except (psycopg.Error, DbError, ValueError) as exc:
            message = getattr(getattr(exc, "diag", None), "message_primary", None) or str(exc)
            log.info("candidate %s could not be tested: %s", candidate.ddl, message)
            results.append(ScoredCandidate(candidate, error=f"could not be tested: {message}"))
        finally:
            _reset(conn)
    return results
