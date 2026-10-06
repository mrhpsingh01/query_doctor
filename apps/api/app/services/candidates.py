"""The candidate stage of the analysis pipeline: generate -> prune -> test with HypoPG -> persist.

A candidate is only ever a suggestion until measured. Here it can reach ``planner_validated`` (the planner would use
it); ``benchmark_validated`` is awarded later by an approved benchmark.
"""

from __future__ import annotations

import logging

from querydoctor.candidates import PlanComparison, ScoredCandidate, generate_candidates, prune_redundant
from querydoctor.plan_parser import ParsedPlan
from sqlalchemy.orm import Session

from app.models import Analysis, RecommendationCandidate

log = logging.getLogger(__name__)

MIN_COST_REDUCTION = 0.10
MIN_OPERATOR_IMPROVEMENT = 0.5
NOT_USED_NOTE = "The planner did not use this index for this query as tested, so it remains only a suggestion."


def _meaningful(c: PlanComparison) -> bool:
    return c.uses_candidate and (c.cost_reduction >= MIN_COST_REDUCTION or c.operator_improvement >= MIN_OPERATOR_IMPROVEMENT)


def _row(analysis: Analysis, scored: ScoredCandidate | None, candidate, *, validation, rank, dropped=False, reason=None):
    comparison = scored.comparison if scored else None
    return RecommendationCandidate(
        analysis_id=analysis.id, candidate_key=candidate.id, table_name=candidate.table, ddl=candidate.ddl,
        columns=[c.model_dump() for c in candidate.columns], include=list(candidate.include),
        predicate=candidate.predicate, expression=candidate.expression, rationale=list(candidate.rationale),
        source_rule_ids=list(candidate.source_rule_ids), validation=validation,
        planner_score=comparison.planner_score if comparison else None,
        comparison=comparison.model_dump() if comparison else None, dropped=dropped, drop_reason=reason, rank=rank,
    )


def run_candidate_stage(session: Session, deps, analysis: Analysis, plan: ParsedPlan, schema, vq, findings) -> None:
    """Never raises: candidates are an optional extra, so a failure here must not fail the diagnosis."""
    try:
        snapshot = analysis.snapshot
        generated = generate_candidates(snapshot.sql, schema, findings)
        kept, pruned = prune_redundant(generated, schema)
        table_rows = {name: info.row_estimate for name, info in schema.tables.items()}

        scored: list[ScoredCandidate] = []
        if kept:
            with deps.connect(deps.target_dsn) as conn:
                # HypoPG only influences plain EXPLAIN, so the baseline must be a planner-only plan.
                baseline = plan if not analysis.execute else _planner_only(deps, conn, snapshot)
                scored = deps.scorer(conn, snapshot.sql, snapshot.params, baseline, kept, table_rows)
    except Exception:
        log.warning("candidate stage failed for analysis %s", analysis.id, exc_info=True)
        session.rollback()
        return

    validated, suggested, dropped = [], [], []
    for s in scored:
        if s.error:
            dropped.append((s, s.error))
        elif not s.comparison.uses_candidate:
            s.candidate.rationale.append(NOT_USED_NOTE)
            suggested.append(s)
        elif _meaningful(s.comparison):
            validated.append(s)
        else:
            dropped.append((s, "the planner's estimated improvement is below the threshold worth an index"))
    validated.sort(key=lambda s: -s.comparison.planner_score)

    rank = 0
    rows = []
    for s in validated:
        rank += 1
        rows.append(_row(analysis, s, s.candidate, validation="planner_validated", rank=rank))
    for s in suggested:
        rank += 1
        rows.append(_row(analysis, s, s.candidate, validation="suggested", rank=rank))
    for s, reason in dropped:
        rows.append(_row(analysis, s, s.candidate, validation="suggested", rank=0, dropped=True, reason=reason))
    for candidate, reason in pruned:
        rows.append(_row(analysis, None, candidate, validation="suggested", rank=0, dropped=True, reason=reason))
    analysis.candidates = rows
    session.commit()


def _planner_only(deps, conn, snapshot) -> ParsedPlan:
    from querydoctor.plan_parser import parse_explain

    return parse_explain(deps.explain(conn, snapshot.sql, snapshot.params, execute=False))
