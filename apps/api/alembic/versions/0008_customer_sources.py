"""Project memberships and engine aware connections.

Revision ID: 0008
Revises: 0007
"""

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("connections", sa.Column("engine", sa.String(length=20), nullable=False, server_default="postgres"))
    op.add_column("connections", sa.Column("tls", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("connections", sa.Column("auth_database", sa.String(length=200), nullable=True))
    op.create_table(
        "project_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="member"),
        sa.UniqueConstraint("project_id", "subject", name="uq_project_member"),
    )
    op.create_index("ix_project_members_project_id", "project_members", ["project_id"])
    op.create_index("ix_project_members_subject", "project_members", ["subject"])
    op.add_column("workload_snapshots", sa.Column("source_connection_id", sa.Integer(), sa.ForeignKey("connections.id", ondelete="SET NULL"), nullable=True))
    op.add_column("workload_snapshots", sa.Column("capture_method", sa.String(length=40), nullable=False, server_default="pg_stat_statements"))
    op.add_column("workload_snapshots", sa.Column("capture_start", sa.DateTime(timezone=True), nullable=True))
    op.add_column("workload_snapshots", sa.Column("capture_end", sa.DateTime(timezone=True), nullable=True))
    op.add_column("workload_snapshots", sa.Column("coverage", sa.String(length=30), nullable=False, server_default="unknown"))
    op.add_column("workload_snapshots", sa.Column("engine_version", sa.String(length=100), nullable=True))
    op.add_column("workload_snapshots", sa.Column("schema_fingerprint", sa.String(length=64), nullable=True))
    op.add_column("workload_snapshots", sa.Column("index_fingerprint", sa.String(length=64), nullable=True))
    op.add_column("workload_snapshots", sa.Column("warnings", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("workload_queries", sa.Column("p95_observed_ms", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("workload_queries", "p95_observed_ms")
    for name in ("warnings", "index_fingerprint", "schema_fingerprint", "engine_version", "coverage", "capture_end", "capture_start", "capture_method", "source_connection_id"):
        op.drop_column("workload_snapshots", name)
    op.drop_index("ix_project_members_subject", table_name="project_members")
    op.drop_index("ix_project_members_project_id", table_name="project_members")
    op.drop_table("project_members")
    op.drop_column("connections", "auth_database")
    op.drop_column("connections", "tls")
    op.drop_column("connections", "engine")
