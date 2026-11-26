"""Join the two migration branches made in parallel.

``feature/architecture-v2`` added sb0006_state_documents -> sb0007_run_jobs and
``feature/databricks-metric-views`` added sb0006_mapping_override_origin ->
... -> sb0011_dbx_revision_bigint, both after sb0005. No revision is renamed:
a database on either branch upgrades through the other branch's migrations
(each is additive and checks before it creates) and lands here, one head.
"""
from __future__ import annotations

revision = "sb0012_merge_branches"
down_revision = ("sb0007_run_jobs", "sb0011_dbx_revision_bigint")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
