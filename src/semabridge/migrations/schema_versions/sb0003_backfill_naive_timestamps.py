"""Repair timestamps written naive into timestamptz columns (provable rows
only -- see repository/maintenance/timestamp_backfill.py for the rules).
PostgreSQL only; a no-op elsewhere and on a UTC server. Back up first."""
from __future__ import annotations

import logging

from alembic import op

revision = "sb0003_tz_backfill"
down_revision = "sb0002_config_overrides"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.sb0003")


def upgrade() -> None:
    from semabridge.repository.maintenance.timestamp_backfill import backfill_naive_timestamps

    report = backfill_naive_timestamps(op.get_bind(), apply=True)
    logger.warning("sb0003 timestamp backfill: %s", report)


def downgrade() -> None:
    # The repair is not reversible without the pre-migration backup.
    pass
