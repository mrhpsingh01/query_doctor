from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedMixin


class Explanation(CreatedMixin, Base):
    """A grounded explanation of an analysis. Only the facts it was built from are stored, never query parameters or rows."""

    __tablename__ = "explanations"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | completed | failed
    source: Mapped[str | None] = mapped_column(String(80), default=None)  # "template" or "ollama:<model>"
    text: Mapped[str | None] = mapped_column(Text, default=None)
    sentences: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)  # [{text, fact_ids}]
    facts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    dropped_sentences: Mapped[list[str]] = mapped_column(JSON, default=list)
    fallback_reason: Mapped[str | None] = mapped_column(Text, default=None)
    reviewed: Mapped[bool | None] = mapped_column(Boolean, default=None)  # a person's accept (True) / reject (False)
    error: Mapped[str | None] = mapped_column(Text, default=None)
