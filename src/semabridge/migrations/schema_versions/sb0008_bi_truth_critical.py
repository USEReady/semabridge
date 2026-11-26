"""BI-Truth Case critical flag and tolerance support.

Adds ``is_critical`` and ``tolerance`` to ``ai_truth_cases``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "sb0008_bi_truth_critical"
down_revision = "sb0007_bi_truth_windows"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    insp = _inspector()
    if insp.has_table("ai_truth_cases"):
        cols = {c["name"] for c in insp.get_columns("ai_truth_cases")}
        if "is_critical" not in cols:
            op.add_column(
                "ai_truth_cases",
                sa.Column("is_critical", sa.Boolean, nullable=False, server_default=sa.false()),
            )
        if "tolerance" not in cols:
            op.add_column(
                "ai_truth_cases",
                sa.Column("tolerance", sa.Float, nullable=True),
            )


def downgrade() -> None:
    insp = _inspector()
    if insp.has_table("ai_truth_cases"):
        cols = {c["name"] for c in insp.get_columns("ai_truth_cases")}
        if "is_critical" in cols:
            op.drop_column("ai_truth_cases", "is_critical")
        if "tolerance" in cols:
            op.drop_column("ai_truth_cases", "tolerance")
