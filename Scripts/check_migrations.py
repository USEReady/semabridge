"""CI check: on the configured database (PostgreSQL in CI), startup's schema
path (create_all, then Alembic) reaches head and a second run changes nothing.

    SEMABRIDGE_DATABASE_URL=postgresql+psycopg2://... python Scripts/check_migrations.py
"""
from __future__ import annotations

import sys

import sqlalchemy as sa

import semabridge.repository.orm  # noqa: F401  (registers every table)
from semabridge.repository.alembic_runner import upgrade_database
from semabridge.repository.orm.base import Base
from semabridge.repository.orm.session_factory import get_engine


def main() -> int:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    first = upgrade_database(engine)
    second = upgrade_database(engine)
    print("first:", first)
    print("second:", second)
    head = first.get("after")
    if not head or second.get("before") != head or second.get("after") != head:
        print("Migrations did not reach a stable head.", file=sys.stderr)
        return 1
    missing = [t for t in ("state_documents", "run_jobs") if not sa.inspect(engine).has_table(t)]
    if missing:
        print(f"Missing tables after migration: {missing}", file=sys.stderr)
        return 1
    print(f"OK: database at {head}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
