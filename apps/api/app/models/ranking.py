from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedMixin


class ModelPrediction(CreatedMixin, Base):
    """What the ranking model expected of one index candidate. Advice only: it never alters the evidence level."""

    __tablename__ = "model_predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("recommendation_candidates.id", ondelete="CASCADE"), unique=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    model_version: Mapped[str] = mapped_column(String(80))
    expected_speedup: Mapped[float] = mapped_column(Float)  # a factor (8.0 = about 8x faster)
    p_improve: Mapped[float] = mapped_column(Float)  # calibrated probability of a >= 20% improvement
    expected_index_mb: Mapped[float] = mapped_column(Float)
    p_regression: Mapped[float] = mapped_column(Float)
    rank_score: Mapped[float] = mapped_column(Float)
    used_for_ranking: Mapped[bool] = mapped_column(Boolean, default=False)  # the model, not the planner score, ordered the list
    contributions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    base_value: Mapped[float] = mapped_column(Float, default=0.0)
    other_effect: Mapped[float] = mapped_column(Float, default=0.0)


class UserFeedback(CreatedMixin, Base):
    __tablename__ = "user_feedback"
    __table_args__ = (UniqueConstraint("candidate_id", "session_id", name="uq_feedback_candidate_session"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("recommendation_candidates.id", ondelete="CASCADE"))
    session_id: Mapped[str] = mapped_column(String(64))
    helpful: Mapped[bool] = mapped_column(Boolean)
    comment: Mapped[str | None] = mapped_column(Text, default=None)


class ModelOutcome(CreatedMixin, Base):
    """A completed benchmark of a candidate that had a prediction: what was predicted against what was measured."""

    __tablename__ = "model_outcomes"

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("recommendation_candidates.id", ondelete="CASCADE"), index=True)
    benchmark_run_id: Mapped[int] = mapped_column(Integer, unique=True)
    model_version: Mapped[str] = mapped_column(String(80))
    predicted_speedup: Mapped[float] = mapped_column(Float)
    actual_speedup: Mapped[float] = mapped_column(Float)
    abs_error_log: Mapped[float] = mapped_column(Float)  # |ln(predicted speedup) - ln(actual speedup)|
    predicted_p_improve: Mapped[float] = mapped_column(Float)
    predicted_positive: Mapped[bool] = mapped_column(Boolean)  # the model said "likely to improve" (p >= 0.5)
    actual_useful: Mapped[bool] = mapped_column(Boolean)  # the benchmark measured >= 20% faster with identical output
