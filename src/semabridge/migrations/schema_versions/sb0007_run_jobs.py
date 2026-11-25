"""Durable work queue (run_jobs) for background project runs.

Workers claim queued rows (FOR UPDATE SKIP LOCKED on PostgreSQL), heartbeat
while executing, and re-queue rows whose worker stopped. Additive.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "sb0007_run_jobs"
down_revision = "sb0006_state_documents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("run_jobs"):
        return
    op.create_table(
        "run_jobs",
        sa.Column("job_id", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(255), nullable=True),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="2"),
        sa.Column("claimed_by", sa.String(128), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_run_jobs_status_created", "run_jobs", ["status", "created_at"])
    op.create_index("ix_run_jobs_project", "run_jobs", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_run_jobs_project", table_name="run_jobs")
    op.drop_index("ix_run_jobs_status_created", table_name="run_jobs")
    op.drop_table("run_jobs")
