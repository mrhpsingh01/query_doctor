"""Erase an analysis and everything stored under it.

Explicit deletes rather than relying on database cascades alone, so the result is the same on every database. The audit
trail keeps its (query-free) events with the link to the analysis cleared, plus one record that the deletion happened.
"""

from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models import (
    Analysis, AuditEvent, BenchmarkRun, Explanation, Finding, ModelOutcome, ModelPrediction, QuerySnapshot,
    RecommendationCandidate, UserFeedback,
)


def delete_everything(session: Session, analysis: Analysis) -> None:
    analysis_id, snapshot_id = analysis.id, analysis.snapshot_id
    candidate_ids = list(session.scalars(select(RecommendationCandidate.id).where(RecommendationCandidate.analysis_id == analysis_id)))
    if candidate_ids:
        session.execute(delete(ModelOutcome).where(ModelOutcome.candidate_id.in_(candidate_ids)))
    for model in (ModelPrediction, UserFeedback, BenchmarkRun, Explanation):
        session.execute(delete(model).where(model.analysis_id == analysis_id))
    session.execute(delete(Finding).where(Finding.analysis_id == analysis_id))
    session.execute(delete(RecommendationCandidate).where(RecommendationCandidate.analysis_id == analysis_id))
    session.execute(update(AuditEvent).where(AuditEvent.analysis_id == analysis_id).values(analysis_id=None))
    session.expire_all()
    session.delete(session.get(Analysis, analysis_id))
    session.flush()
    others = session.scalar(select(Analysis.id).where(Analysis.snapshot_id == snapshot_id).limit(1))
    if others is None:  # the canonical query and its parameters go too, once nothing else refers to them
        session.execute(delete(QuerySnapshot).where(QuerySnapshot.id == snapshot_id))
    session.add(AuditEvent(event_type="analysis_deleted", detail={"analysis_id": analysis_id}))
    session.commit()
