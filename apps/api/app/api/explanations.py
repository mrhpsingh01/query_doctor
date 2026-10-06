from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictBool
from sqlalchemy.orm import Session

from app.deps import get_session
from app.models import Analysis, AuditEvent, Explanation

router = APIRouter(tags=["explanations"])


class ExplanationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["plan", "findings", "tradeoffs", "next_experiment", "pr_comment"]


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: StrictBool


class ExplanationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    analysis_id: int
    kind: str
    status: str
    source: str | None
    text: str | None
    sentences: list[dict]
    facts: list[dict]
    dropped_sentences: list[str]
    fallback_reason: str | None
    reviewed: bool | None
    error: str | None


@router.post("/api/analyses/{analysis_id}/explanations", status_code=202)
def create_explanation(analysis_id: int, body: ExplanationRequest, request: Request, session: Session = Depends(get_session)):
    analysis = session.get(Analysis, analysis_id)
    if analysis is None:
        raise HTTPException(404, detail="analysis not found")
    if analysis.status != "completed":
        raise HTTPException(409, detail="the analysis has not finished yet")
    row = Explanation(analysis_id=analysis_id, kind=body.kind, status="queued")
    session.add(row)
    session.flush()
    session.add(AuditEvent(analysis_id=analysis_id, event_type="explanation_requested", detail={"explanation_id": row.id, "kind": body.kind}))
    session.commit()
    request.app.state.enqueue_explanation(row.id)
    session.refresh(row)
    return {"id": row.id, "status": row.status}


@router.get("/api/explanations/{explanation_id}", response_model=ExplanationOut)
def get_explanation(explanation_id: int, session: Session = Depends(get_session)):
    row = session.get(Explanation, explanation_id)
    if row is None:
        raise HTTPException(404, detail="explanation not found")
    return row


@router.post("/api/explanations/{explanation_id}/review")
def review_explanation(explanation_id: int, body: ReviewRequest, session: Session = Depends(get_session)):
    """A person's decision on an explanation. Nothing an explanation says is ever executed."""
    row = session.get(Explanation, explanation_id)
    if row is None:
        raise HTTPException(404, detail="explanation not found")
    row.reviewed = body.accepted
    session.add(AuditEvent(analysis_id=row.analysis_id, event_type="explanation_reviewed", detail={"explanation_id": row.id, "accepted": body.accepted}))
    session.commit()
    return {"id": row.id, "reviewed": row.reviewed}
