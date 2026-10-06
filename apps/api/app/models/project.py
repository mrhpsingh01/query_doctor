from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedMixin


class Project(CreatedMixin, Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, default=None)


class Connection(CreatedMixin, Base):
    """A target database. Passwords are only ever stored encrypted (see app.security.crypto)."""

    __tablename__ = "connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="sample")
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer)
    database: Mapped[str] = mapped_column(String(200))
    username: Mapped[str] = mapped_column(String(200))
    encrypted_password: Mapped[str] = mapped_column(Text)  # empty once the connection is deleted
    sslmode: Mapped[str] = mapped_column(String(12), default="prefer")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
