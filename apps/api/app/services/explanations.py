"""Run an explanation job: build the analysis dict, ask the explainer (template, or a local model with fallback), store it."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from querydoctor.explain import OllamaExplainer, build_evidence, explain
from sqlalchemy.orm import Session

from app.models import Analysis, Explanation
from app.schemas.analysis import AnalysisOut

log = logging.getLogger(__name__)

FAILED_MESSAGE = "The explanation could not be produced. Try again."


@dataclass
class ExplainDeps:
    ollama: OllamaExplainer | None = None  # None or an empty model name means the template explainer only

    @property
    def uses_model(self) -> bool:
        return self.ollama is not None and bool(getattr(self.ollama, "enabled", False))


def run_explanation_job(session: Session, explanation_id: int, deps: ExplainDeps) -> None:
    row = session.get(Explanation, explanation_id)
    if row is None or row.status != "queued":
        return
    try:
        analysis = session.get(Analysis, row.analysis_id)
        data = AnalysisOut.from_model(analysis).model_dump(mode="json")
        result = explain(data, row.kind, ollama=deps.ollama)
        pkg = build_evidence(data, row.kind)
        row.source, row.text = result.source, result.text
        row.sentences = [s.model_dump() for s in result.sentences]
        row.facts = [f.model_dump() for f in pkg.facts]
        row.dropped_sentences, row.fallback_reason = list(result.dropped), result.fallback_reason
        row.status = "completed"
    except Exception:
        log.exception("explanation %s failed", explanation_id)
        session.rollback()
        row = session.get(Explanation, explanation_id)
        row.status, row.error = "failed", FAILED_MESSAGE
    session.commit()
