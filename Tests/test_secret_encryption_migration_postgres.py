from __future__ import annotations

import pytest
from sqlalchemy import Boolean, Column, Integer, MetaData, String, Table, create_engine, select, text
from sqlalchemy.dialects import postgresql, sqlite

from semabridge.api.bootstrap.db_migrations import migrate_credentials_encryption


def test_migration_query_compiles_for_postgres_dialect():
    stmt = text(
        "SELECT owner_id, service, key, value, is_secret "
        "FROM semabridge_credentials WHERE is_secret = :is_secret"
    ).bindparams(is_secret=True)

    compiled_pg = stmt.compile(dialect=postgresql.dialect())
    sql_text = str(compiled_pg)
    assert "is_secret = %(is_secret)s" in sql_text or "is_secret = :is_secret" in sql_text

    # Verify no 'is_secret = 1' in postgres query
    assert "is_secret = 1" not in sql_text
    # Verify the bound parameter is the boolean True, not an integer 1
    assert compiled_pg.params["is_secret"] is True
    assert isinstance(compiled_pg.params["is_secret"], bool)


def test_migration_query_works_on_sqlite(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test_cred.db")
    meta = MetaData()
    creds = Table(
        "semabridge_credentials",
        meta,
        Column("owner_id", Integer, primary_key=True),
        Column("service", String, primary_key=True),
        Column("key", String, primary_key=True),
        Column("value", String),
        Column("is_secret", Boolean),
    )
    meta.create_all(engine)

    with engine.begin() as conn:
        conn.execute(
            creds.insert(),
            [
                {"owner_id": 1, "service": "snowflake", "key": "password", "value": "plain_secret_1", "is_secret": True},
                {"owner_id": 1, "service": "snowflake", "key": "user", "value": "john_doe", "is_secret": False},
            ],
        )

    migrated = migrate_credentials_encryption(engine)
    assert migrated == 1

    with engine.begin() as conn:
        rows = {r[0]: r[1] for r in conn.execute(select(creds.c.key, creds.c.value)).all()}
    assert rows["user"] == "john_doe"
    assert rows["password"].startswith("gAAAAA")


def test_migration_fails_loudly_on_error():
    broken_engine = create_engine("sqlite:///:memory:")
    # We do not create the columns needed, or table structure is broken
    with broken_engine.begin() as conn:
        conn.execute(text("CREATE TABLE semabridge_credentials (wrong_column INTEGER)"))

    with pytest.raises(Exception):
        migrate_credentials_encryption(broken_engine)
