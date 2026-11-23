"""Tests ensuring the credentials encryption migration prevents double encryption.

Requirements:
- A value already looking like a Fernet token (encrypted with another key) stays byte-identical.
- Running the migration twice changes nothing.
- A warning is logged naming the service and key (never the value).
"""

import logging
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text

from semabridge.api.bootstrap.db_migrations import migrate_credentials_encryption


def test_value_encrypted_with_another_key_stays_byte_identical_and_warns(caplog):
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE semabridge_credentials ("
                "owner_id INTEGER, service TEXT, key TEXT, value TEXT, is_secret BOOLEAN"
                ")"
            )
        )
        foreign_key = Fernet.generate_key()
        foreign_token = Fernet(foreign_key).encrypt(b"secret_value_123").decode("utf-8")
        conn.execute(
            text(
                "INSERT INTO semabridge_credentials (owner_id, service, key, value, is_secret) "
                "VALUES (:owner_id, :service, :key, :value, :is_secret)"
            ),
            {
                "owner_id": 1,
                "service": "test_fabric",
                "key": "client_secret",
                "value": foreign_token,
                "is_secret": True,
            },
        )

    with caplog.at_level(logging.WARNING):
        migrated = migrate_credentials_encryption(engine)

    assert migrated == 0

    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT value FROM semabridge_credentials WHERE service = 'test_fabric'")
        ).fetchone()
        assert row[0] == foreign_token  # Byte-identical

    # Run migration a second time: changes nothing, stays byte-identical
    migrated_second = migrate_credentials_encryption(engine)
    assert migrated_second == 0
    with engine.begin() as conn:
        row_second = conn.execute(
            text("SELECT value FROM semabridge_credentials WHERE service = 'test_fabric'")
        ).fetchone()
        assert row_second[0] == foreign_token

    # Assert warning logged naming service and key, but NOT the token or secret value
    assert any(
        "test_fabric" in r.message and "client_secret" in r.message and "secret_value_123" not in r.message
        for r in caplog.records
    )


def test_running_migration_twice_changes_nothing():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE semabridge_credentials ("
                "owner_id INTEGER, service TEXT, key TEXT, value TEXT, is_secret BOOLEAN"
                ")"
            )
        )
        conn.execute(
            text(
                "INSERT INTO semabridge_credentials (owner_id, service, key, value, is_secret) "
                "VALUES (:owner_id, :service, :key, :value, :is_secret)"
            ),
            {
                "owner_id": 1,
                "service": "snowflake",
                "key": "password",
                "value": "super_secret_plain",
                "is_secret": True,
            },
        )

    # First run encrypts
    migrated1 = migrate_credentials_encryption(engine)
    assert migrated1 == 1

    with engine.begin() as conn:
        row1 = conn.execute(
            text("SELECT value FROM semabridge_credentials WHERE service = 'snowflake'")
        ).fetchone()
        encrypted_val1 = row1[0]
        assert encrypted_val1.startswith("gAAAAA")
        assert encrypted_val1 != "super_secret_plain"

    # Second run changes nothing
    migrated2 = migrate_credentials_encryption(engine)
    assert migrated2 == 0

    with engine.begin() as conn:
        row2 = conn.execute(
            text("SELECT value FROM semabridge_credentials WHERE service = 'snowflake'")
        ).fetchone()
        assert row2[0] == encrypted_val1  # Unchanged
