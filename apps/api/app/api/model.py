from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.deps import get_session
from app.models import Analysis, RecommendationCandidate, UserFeedback
from app.services import ranking

router = APIRouter(tags=["model"])


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    helpful: StrictBool
    comment: str | None = Field(default=None, max_length=500)


def _meta(request: Request) -> dict | None:
    predictor = getattr(request.app.state.pipeline_deps, "predictor", None)
    return getattr(predictor, "meta", None)


@router.get("/api/model")
def get_model(request: Request):
    """The training metadata of the loaded ranking model, or ``{"available": false}`` when there is none."""
    meta = _meta(request)
    return {"available": False} if meta is None else {"available": True, **meta}


@router.get("/api/model/monitor")
def get_monitor(request: Request, session: Session = Depends(get_session)):
    return ranking.monitor(session, _meta(request))


@router.post("/api/analyses/{analysis_id}/candidates/{candidate_id}/feedback")
def give_feedback(
    analysis_id: int,
    candidate_id: int,
    body: FeedbackIn,
    session: Session = Depends(get_session),
    x_qd_session: str | None = Header(default=None, max_length=64),
):
    """Thumbs up or down on an index idea. One row per candidate and browser session: voting again changes the vote."""
    if session.get(Analysis, analysis_id) is None:
        raise HTTPException(404, detail="analysis not found")
    candidate = session.get(RecommendationCandidate, candidate_id)
    if candidate is None or candidate.analysis_id != analysis_id:
        raise HTTPException(404, detail="index idea not found")
    who = x_qd_session or "anonymous"
    comment = (body.comment or "").strip() or None
    row = session.scalar(select(UserFeedback).where(UserFeedback.candidate_id == candidate_id, UserFeedback.session_id == who))
    if row is None:
        row = UserFeedback(analysis_id=analysis_id, candidate_id=candidate_id, session_id=who, helpful=body.helpful, comment=comment)
        session.add(row)
    else:
        row.helpful, row.comment = body.helpful, comment
    session.commit()
    return {"id": row.id, "helpful": row.helpful}
