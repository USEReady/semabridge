"""BI-Truth Case time windows support.

Adds ``windows_json`` to ``ai_truth_cases`` to store defined time slices per test case.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "sb0007_bi_truth_windows"
down_revision = "sb0006_drift_platform_adapter"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    insp = _inspector()
    if insp.has_table("ai_truth_cases"):
        cols = {c["name"] for c in insp.get_columns("ai_truth_cases")}
        if "windows_json" not in cols:
            op.add_column(
                "ai_truth_cases",
                sa.Column("windows_json", sa.Text, nullable=False, server_default="[]"),
            )


def downgrade() -> None:
    insp = _inspector()
    if insp.has_table("ai_truth_cases"):
        cols = {c["name"] for c in insp.get_columns("ai_truth_cases")}
        if "windows_json" in cols:
            op.drop_column("ai_truth_cases", "windows_json")
