"""Baseline: the schema as it existed when Alembic was adopted (2026-11).

Existing databases (schema built by ``Base.metadata.create_all`` plus the
hand-written idempotent column adds in ``api/bootstrap/db_migrations.py``)
are STAMPED at this revision instead of running it. A brand-new database runs
it: ``create_all`` of every ORM table (the ORM models are the baseline
definition) followed by the same idempotent column/compat fixes.

The legacy revision files in ``migrations/versions`` were never applied to
any database (no ``alembic_version`` table existed, and they contain a
duplicated revision id and five heads); they are kept for reference only and
are not on this chain.
"""
from __future__ import annotations

from alembic import op

revision = "sb0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    import semabridge.repository.orm.cache_models  # noqa: F401
    import semabridge.repository.orm.models  # noqa: F401
    from semabridge.repository.orm.base import Base

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    raise NotImplementedError("The baseline revision cannot be downgraded.")
