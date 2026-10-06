"""The candidate stage of the analysis pipeline: generate -> prune -> test with HypoPG -> persist.

A candidate is only ever a suggestion until measured. Here it can reach ``planner_validated`` (the planner would use
it); ``benchmark_validated`` is awarded later by an approved benchmark.
"""

from __future__ import annotations

import logging

from querydoctor.candidates import PlanComparison, ScoredCandidate
from querydoctor.plan_parser import ParsedPlan
from sqlalchemy.orm import Session

from app.models import Analysis, RecommendationCandidate
from app.services import ranking
from app.services.adapters import adapter_for
from app.services.pipeline import dsn_for

log = logging.getLogger(__name__)

MIN_COST_REDUCTION = 0.10
MIN_OPERATOR_IMPROVEMENT = 0.5
HYPOPG_MISSING_NOTE = "Index testing is unavailable on this database (the hypopg extension is not installed), so this remains only a suggestion."
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
        adapter = adapter_for(deps, analysis.engine)
        kept, pruned = adapter.generate_candidates(vq, schema, findings)

        scored: list[ScoredCandidate] = []
        feature_schema, baseline = schema, plan
        if kept:
            with deps.connect(dsn_for(session, deps, analysis)) as conn:
                # HypoPG only influences plain EXPLAIN, so the baseline must be a planner-only plan.
                baseline = plan if not analysis.execute else adapter.normalize_plan(adapter.explain(conn, vq, snapshot.params, execute=False))
                if analysis.connection_id is not None and not deps.hypopg_available(conn):
                    # Index testing needs HypoPG on the target. Without it every idea stays what it is: a suggestion.
                    for c in kept:
                        c.rationale.append(HYPOPG_MISSING_NOTE)
                    scored = [ScoredCandidate(c) for c in kept]
                else:
                    scored = adapter.test_candidates(conn, vq, snapshot.params, baseline, kept, schema)
                if getattr(deps, "predictor", None) is not None and analysis.connection_id is None:
                    feature_schema = ranking.schema_with_extras(deps, conn, schema, list(schema.tables))
    except Exception:
        log.warning("candidate stage failed for analysis %s", analysis.id, exc_info=True)
        session.rollback()
        return

    validated, suggested, dropped = [], [], []
    for s in scored:
        if s.error:
            dropped.append((s, s.error))
        elif s.comparison is None:  # not testable on this database (no HypoPG): the note is already on the candidate
            suggested.append(s)
        elif not s.comparison.uses_candidate:
            s.candidate.rationale.append(NOT_USED_NOTE)
            suggested.append(s)
        elif _meaningful(s.comparison):
            validated.append(s)
        else:
            dropped.append((s, "the planner's estimated improvement is below the threshold worth an index"))
    validated.sort(key=lambda s: -s.comparison.planner_score)

    # Advice from the ranking model: attached to every testable candidate, and only when it has proven itself against the
    # planner-score heuristic (use_for_ranking) does it order the planner-validated ones. Never touches `validation`.
    predictor = getattr(deps, "predictor", None)
    predictions = ranking.predict_candidates(
        predictor if analysis.connection_id is None else None, snapshot.sql, feature_schema, baseline, [*validated, *suggested]
    )
    model_orders = bool(predictions) and bool(getattr(predictor, "use_for_ranking", False))
    if model_orders:
        validated.sort(key=lambda s: -predictions[s.candidate.id].rank_score)

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
    if predictions:
        ranking.persist_predictions(session, analysis, predictions, model_orders)
