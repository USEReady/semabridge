"""
Test suite for repository database abstraction layer (db.py).

Tests both DuckDB and ORM backends via the factory function,
ensuring the Protocol contract is satisfied by each implementation.

Coverage
--------
* DuckDB backend — execute / fetchall / fetchone / params / transactions
* ORM backend    — SQLite in-memory (always runs)
                 — DuckDB via duckdb-engine (skipped when driver absent)
                 — PostgreSQL (skipped unless POSTGRES_TEST_URL is set)
                 — Snowflake  (skipped unless SNOWFLAKE_TEST_URL is set)
* ORM Models     — User · Post · UserCredential · ModelVersionHistory
                   full CRUD + relationship integrity via SQLite session
* Session Factory — get_engine / get_session_factory / reset_engine
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Generator

import duckdb
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from semabridge.repository.db import (
    DuckDBBackend,
    ORMBackend,
    create_repository_backend,
)
from semabridge.repository.orm.base import Base
from semabridge.repository.orm.models import (
    ModelVersionHistory,
    Post,
    User,
    UserCredential,
)
from semabridge.repository.orm.session_factory import (
    get_engine,
    get_session_factory,
    reset_engine,
)


# ---------------------------------------------------------------------------
# Helpers / environment-based skip markers
# ---------------------------------------------------------------------------

POSTGRES_URL = os.environ.get("POSTGRES_TEST_URL", "")
SNOWFLAKE_URL = os.environ.get("SNOWFLAKE_TEST_URL", "")

skip_postgres = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="Set POSTGRES_TEST_URL to run PostgreSQL ORM tests",
)
skip_snowflake = pytest.mark.skipif(
    not SNOWFLAKE_URL,
    reason="Set SNOWFLAKE_TEST_URL to run Snowflake ORM tests",
)

try:
    import duckdb_engine  # noqa: F401
    _DUCKDB_ENGINE_AVAILABLE = True
except ImportError:
    _DUCKDB_ENGINE_AVAILABLE = False

skip_duckdb_engine = pytest.mark.skipif(
    not _DUCKDB_ENGINE_AVAILABLE,
    reason="duckdb-engine not installed; skipping DuckDB-via-SQLAlchemy tests",
)


# ---------------------------------------------------------------------------
# Fixtures — DuckDB (raw)
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db_path(tmp_path: Path) -> str:
    """Provide a temporary DuckDB file path."""
    return str(tmp_path / "test_repo.db")


@pytest.fixture
def duckdb_backend(tmp_db_path: str) -> Generator[DuckDBBackend, None, None]:
    """Create a DuckDB backend with a test schema."""
    backend = DuckDBBackend(tmp_db_path)
    backend.execute("""
        CREATE TABLE IF NOT EXISTS test_items (
            id INTEGER PRIMARY KEY,
            name VARCHAR NOT NULL,
            value DOUBLE
        );
    """)
    yield backend
    backend.close()


# ---------------------------------------------------------------------------
# Fixtures — ORM (SQLAlchemy)
# ---------------------------------------------------------------------------

@pytest.fixture
def sqlite_url() -> str:
    """In-memory SQLite URL (no file, nothing to clean up)."""
    return "sqlite://"


@pytest.fixture
def sqlite_engine(sqlite_url: str):
    """SQLAlchemy engine wired to in-memory SQLite."""
    engine = create_engine(sqlite_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def sqlite_session(sqlite_engine) -> Generator[Session, None, None]:
    """Scoped SQLAlchemy session for model CRUD tests."""
    SessionLocal = sessionmaker(bind=sqlite_engine, expire_on_commit=False)
    with SessionLocal() as session:
        yield session


@pytest.fixture
def orm_backend_sqlite(sqlite_url: str) -> Generator[ORMBackend, None, None]:
    """ORMBackend wired to SQLite for low-level backend tests."""
    backend = ORMBackend(sqlite_url)
    # Bootstrap a simple test table
    backend._conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS test_items "
            "(id INTEGER PRIMARY KEY, name TEXT NOT NULL, value REAL)"
        )
    )
    backend._conn.commit()
    yield backend
    backend.close()


# ---------------------------------------------------------------------------
# DuckDB Backend Tests (raw DuckDB API)
# ---------------------------------------------------------------------------

class TestDuckDBBackend:
    """Test the DuckDB backend implementation."""

    def test_init_creates_connection(self, tmp_db_path: str) -> None:
        """DuckDB backend should initialize and connect to the given path."""
        backend = DuckDBBackend(tmp_db_path)
        assert backend is not None
        backend.close()

    def test_execute_and_fetchall(self, duckdb_backend: DuckDBBackend) -> None:
        """Should insert and retrieve data correctly."""
        duckdb_backend.execute(
            "INSERT INTO test_items VALUES (1, 'Alpha', 10.5)"
        )
        rows = duckdb_backend.fetchall("SELECT * FROM test_items WHERE id = 1")
        assert len(rows) == 1
        assert rows[0][1] == "Alpha"
        assert rows[0][2] == 10.5

    def test_fetchone_returns_tuple(self, duckdb_backend: DuckDBBackend) -> None:
        """fetchone should return a single tuple or None."""
        duckdb_backend.execute(
            "INSERT INTO test_items VALUES (2, 'Beta', 20.0)"
        )
        row = duckdb_backend.fetchone("SELECT name FROM test_items WHERE id = 2")
        assert row is not None
        assert row[0] == "Beta"

    def test_fetchone_returns_none_for_missing(self, duckdb_backend: DuckDBBackend) -> None:
        """fetchone should return None when no rows match."""
        row = duckdb_backend.fetchone("SELECT * FROM test_items WHERE id = 999")
        assert row is None

    def test_parameterized_queries(self, duckdb_backend: DuckDBBackend) -> None:
        """Should support parameterized queries to prevent SQL injection."""
        duckdb_backend.execute(
            "INSERT INTO test_items VALUES (?, ?, ?)", [3, "Gamma", 30.0]
        )
        row = duckdb_backend.fetchone(
            "SELECT name FROM test_items WHERE id = ?", [3]
        )
        assert row is not None
        assert row[0] == "Gamma"

    def test_transaction_context_manager(self, duckdb_backend: DuckDBBackend) -> None:
        """Transaction context manager should commit on success."""
        with duckdb_backend.transaction():
            duckdb_backend._conn.execute(
                "INSERT INTO test_items VALUES (4, 'Delta', 40.0)"
            )

        row = duckdb_backend.fetchone("SELECT name FROM test_items WHERE id = 4")
        assert row is not None
        assert row[0] == "Delta"

    def test_transaction_rollback_on_error(self, duckdb_backend: DuckDBBackend) -> None:
        """Transaction should roll back on exception."""
        try:
            with duckdb_backend.transaction():
                duckdb_backend._conn.execute(
                    "INSERT INTO test_items VALUES (5, 'Epsilon', 50.0)"
                )
                raise ValueError("Intentional failure")
        except ValueError:
            pass

        row = duckdb_backend.fetchone("SELECT * FROM test_items WHERE id = 5")
        assert row is None  # Should have been rolled back

    def test_multiple_rows_fetchall(self, duckdb_backend: DuckDBBackend) -> None:
        """fetchall should return every inserted row."""
        for i, name in enumerate(["X", "Y", "Z"], start=10):
            duckdb_backend.execute(
                "INSERT INTO test_items VALUES (?, ?, ?)", [i, name, float(i)]
            )
        rows = duckdb_backend.fetchall(
            "SELECT id FROM test_items WHERE id >= 10 ORDER BY id"
        )
        assert [r[0] for r in rows] == [10, 11, 12]

    def test_close_is_idempotent(self, tmp_db_path: str) -> None:
        """Calling close() twice should not raise."""
        backend = DuckDBBackend(tmp_db_path)
        backend.close()
        # Second close should not raise
        try:
            backend.close()
        except Exception:  # noqa: BLE001
            pytest.fail("Second close() raised unexpectedly")


# ---------------------------------------------------------------------------
# ORM Backend Tests — SQLite (always runs, no external deps)
# ---------------------------------------------------------------------------

class TestORMBackendSQLite:
    """Test ORMBackend wired to in-memory SQLite."""

    def test_init_creates_backend(self, sqlite_url: str) -> None:
        """ORMBackend should initialize without errors on SQLite."""
        backend = ORMBackend(sqlite_url)
        assert backend is not None
        backend.close()

    def test_execute_and_fetchall(self, orm_backend_sqlite: ORMBackend) -> None:
        """Should insert and retrieve rows through the ORM backend."""
        orm_backend_sqlite._conn.execute(
            text("INSERT INTO test_items (id, name, value) VALUES (1, 'Alpha', 10.5)")
        )
        orm_backend_sqlite._conn.commit()
        rows = orm_backend_sqlite.fetchall("SELECT id, name, value FROM test_items WHERE id = 1")
        assert len(rows) == 1
        assert rows[0][1] == "Alpha"
        assert rows[0][2] == pytest.approx(10.5)

    def test_fetchone_returns_tuple(self, orm_backend_sqlite: ORMBackend) -> None:
        """fetchone should return a tuple for a matching row."""
        orm_backend_sqlite._conn.execute(
            text("INSERT INTO test_items (id, name, value) VALUES (2, 'Beta', 20.0)")
        )
        orm_backend_sqlite._conn.commit()
        row = orm_backend_sqlite.fetchone("SELECT name FROM test_items WHERE id = 2")
        assert row is not None
        assert row[0] == "Beta"

    def test_fetchone_returns_none_for_missing(self, orm_backend_sqlite: ORMBackend) -> None:
        """fetchone should return None when no rows match."""
        row = orm_backend_sqlite.fetchone("SELECT * FROM test_items WHERE id = 999")
        assert row is None

    def test_fetchall_empty_table(self, orm_backend_sqlite: ORMBackend) -> None:
        """fetchall on empty table should return an empty list."""
        rows = orm_backend_sqlite.fetchall("SELECT * FROM test_items")
        assert rows == []

    def test_transaction_commit(self, orm_backend_sqlite: ORMBackend) -> None:
        """begin_transaction / commit should persist the row."""
        orm_backend_sqlite.begin_transaction()
        orm_backend_sqlite._conn.execute(
            text("INSERT INTO test_items (id, name, value) VALUES (3, 'Gamma', 30.0)")
        )
        orm_backend_sqlite.commit()

        row = orm_backend_sqlite.fetchone("SELECT name FROM test_items WHERE id = 3")
        assert row is not None
        assert row[0] == "Gamma"

    def test_transaction_rollback(self, orm_backend_sqlite: ORMBackend) -> None:
        """begin_transaction / rollback should discard the row."""
        orm_backend_sqlite.begin_transaction()
        orm_backend_sqlite._conn.execute(
            text("INSERT INTO test_items (id, name, value) VALUES (4, 'Delta', 40.0)")
        )
        orm_backend_sqlite.rollback()

        row = orm_backend_sqlite.fetchone("SELECT * FROM test_items WHERE id = 4")
        assert row is None

    def test_multiple_rows_fetchall(self, orm_backend_sqlite: ORMBackend) -> None:
        """fetchall should return all matching rows in order."""
        for i, n in [(10, "X"), (11, "Y"), (12, "Z")]:
            orm_backend_sqlite._conn.execute(
                text(f"INSERT INTO test_items (id, name, value) VALUES ({i}, '{n}', {float(i)})")
            )
        orm_backend_sqlite._conn.commit()

        rows = orm_backend_sqlite.fetchall(
            "SELECT id FROM test_items WHERE id >= 10 ORDER BY id"
        )
        assert [r[0] for r in rows] == [10, 11, 12]

    def test_execute_positional_params(self, orm_backend_sqlite: ORMBackend) -> None:
        """execute() must handle DuckDB-style positional '?' placeholders."""
        # Bootstrap a row first using raw text so we have something to query
        orm_backend_sqlite._conn.execute(
            text("INSERT INTO test_items (id, name, value) VALUES (99, 'Positional', 9.9)")
        )
        orm_backend_sqlite._conn.commit()

        # This is the API that broke before the fix:
        # ORMBackend.execute() with positional params should not raise.
        result = orm_backend_sqlite.execute(
            "SELECT name FROM test_items WHERE id = ?", [99]
        )
        rows = result.fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "Positional"

    def test_fetchone_positional_params(self, orm_backend_sqlite: ORMBackend) -> None:
        """fetchone() must propagate positional params to execute()."""
        orm_backend_sqlite._conn.execute(
            text("INSERT INTO test_items (id, name, value) VALUES (98, 'FetchOne', 8.8)")
        )
        orm_backend_sqlite._conn.commit()
        row = orm_backend_sqlite.fetchone(
            "SELECT name FROM test_items WHERE id = ?", [98]
        )
        assert row is not None
        assert row[0] == "FetchOne"

    def test_fetchall_positional_params(self, orm_backend_sqlite: ORMBackend) -> None:
        """fetchall() must propagate positional params to execute()."""
        for pk, name in [(97, "FA1"), (96, "FA2")]:
            orm_backend_sqlite._conn.execute(
                text(f"INSERT INTO test_items (id, name, value) VALUES ({pk}, '{name}', 0.0)")
            )
        orm_backend_sqlite._conn.commit()
        rows = orm_backend_sqlite.fetchall(
            "SELECT name FROM test_items WHERE id < ? ORDER BY id DESC", [98]
        )
        names = [r[0] for r in rows if r[0] in ("FA1", "FA2")]
        assert set(names) == {"FA1", "FA2"}


# ---------------------------------------------------------------------------
# ORM Backend Tests — DuckDB via duckdb-engine (optional)
# ---------------------------------------------------------------------------

@skip_duckdb_engine
class TestORMBackendDuckDB:
    """ORMBackend wired to DuckDB via the duckdb-engine SQLAlchemy dialect."""

    @pytest.fixture
    def duckdb_orm_url(self, tmp_path: Path) -> str:
        return f"duckdb:///{tmp_path / 'orm_test.duckdb'}"

    @pytest.fixture
    def duckdb_orm_backend(self, duckdb_orm_url: str) -> Generator[ORMBackend, None, None]:
        backend = ORMBackend(duckdb_orm_url)
        backend._conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS test_items "
                "(id INTEGER PRIMARY KEY, name VARCHAR, value DOUBLE)"
            )
        )
        backend._conn.commit()
        yield backend
        backend.close()

    def test_init(self, duckdb_orm_url: str) -> None:
        backend = ORMBackend(duckdb_orm_url)
        assert backend is not None
        backend.close()

    def test_insert_and_fetch(self, duckdb_orm_backend: ORMBackend) -> None:
        duckdb_orm_backend._conn.execute(
            text("INSERT INTO test_items VALUES (1, 'Duck', 9.9)")
        )
        duckdb_orm_backend._conn.commit()
        row = duckdb_orm_backend.fetchone("SELECT name FROM test_items WHERE id = 1")
        assert row is not None
        assert row[0] == "Duck"

    def test_fetchall_multiple_rows(self, duckdb_orm_backend: ORMBackend) -> None:
        for i in range(3):
            duckdb_orm_backend._conn.execute(
                text(f"INSERT INTO test_items VALUES ({i}, 'R{i}', {float(i)})")
            )
        duckdb_orm_backend._conn.commit()
        rows = duckdb_orm_backend.fetchall("SELECT id FROM test_items ORDER BY id")
        assert len(rows) == 3


# ---------------------------------------------------------------------------
# ORM Backend Tests — PostgreSQL (skipped unless POSTGRES_TEST_URL is set)
# ---------------------------------------------------------------------------

@skip_postgres
class TestORMBackendPostgres:
    """ORMBackend wired to a real PostgreSQL database.

    Set ``POSTGRES_TEST_URL`` to a valid URL, e.g.:
        postgresql+psycopg2://user:pass@localhost/test_db
    """

    @pytest.fixture
    def pg_backend(self) -> Generator[ORMBackend, None, None]:
        backend = ORMBackend(POSTGRES_URL)
        backend._conn.execute(text(
            "CREATE TABLE IF NOT EXISTS _sema_test_items "
            "(id SERIAL PRIMARY KEY, name TEXT NOT NULL, value FLOAT)"
        ))
        backend._conn.commit()
        yield backend
        backend._conn.execute(text("DROP TABLE IF EXISTS _sema_test_items"))
        backend._conn.commit()
        backend.close()

    def test_insert_and_fetch(self, pg_backend: ORMBackend) -> None:
        pg_backend._conn.execute(
            text("INSERT INTO _sema_test_items (name, value) VALUES ('PgRow', 42.0)")
        )
        pg_backend._conn.commit()
        rows = pg_backend.fetchall("SELECT name FROM _sema_test_items WHERE name = 'PgRow'")
        assert any(r[0] == "PgRow" for r in rows)

    def test_fetchone_missing(self, pg_backend: ORMBackend) -> None:
        row = pg_backend.fetchone("SELECT * FROM _sema_test_items WHERE id = -1")
        assert row is None

    def test_transaction_rollback(self, pg_backend: ORMBackend) -> None:
        pg_backend.begin_transaction()
        pg_backend._conn.execute(
            text("INSERT INTO _sema_test_items (name, value) VALUES ('Rollback', 0.0)")
        )
        pg_backend.rollback()
        rows = pg_backend.fetchall(
            "SELECT * FROM _sema_test_items WHERE name = 'Rollback'"
        )
        assert rows == []


# ---------------------------------------------------------------------------
# ORM Backend Tests — Snowflake (skipped unless SNOWFLAKE_TEST_URL is set)
# ---------------------------------------------------------------------------

@skip_snowflake
class TestORMBackendSnowflake:
    """ORMBackend wired to a real Snowflake warehouse.

    Set ``SNOWFLAKE_TEST_URL`` to a valid URL, e.g.:
        snowflake://user:pass@account/db/schema?warehouse=WH
    """

    @pytest.fixture
    def sf_backend(self) -> Generator[ORMBackend, None, None]:
        backend = ORMBackend(SNOWFLAKE_URL)
        backend._conn.execute(text(
            "CREATE TABLE IF NOT EXISTS _SEMA_TEST_ITEMS "
            "(ID NUMBER AUTOINCREMENT PRIMARY KEY, NAME TEXT, VALUE FLOAT)"
        ))
        backend._conn.commit()
        yield backend
        backend._conn.execute(text("DROP TABLE IF EXISTS _SEMA_TEST_ITEMS"))
        backend._conn.commit()
        backend.close()

    def test_insert_and_fetch(self, sf_backend: ORMBackend) -> None:
        sf_backend._conn.execute(
            text("INSERT INTO _SEMA_TEST_ITEMS (NAME, VALUE) VALUES ('SfRow', 7.0)")
        )
        sf_backend._conn.commit()
        rows = sf_backend.fetchall("SELECT NAME FROM _SEMA_TEST_ITEMS WHERE NAME = 'SfRow'")
        assert any(r[0] == "SfRow" for r in rows)

    def test_fetchone_missing(self, sf_backend: ORMBackend) -> None:
        row = sf_backend.fetchone("SELECT * FROM _SEMA_TEST_ITEMS WHERE ID = -999")
        assert row is None


# ---------------------------------------------------------------------------
# ORM Model Tests — full CRUD via SQLAlchemy session (SQLite in-memory)
# ---------------------------------------------------------------------------

class TestUserModel:
    """CRUD tests for the User ORM model."""

    def test_create_user(self, sqlite_session: Session) -> None:
        user = User(
            username="alice",
            email="alice@example.com",
            password_hash="hashed_pw",
            role="admin",
        )
        sqlite_session.add(user)
        sqlite_session.commit()
        assert user.id is not None

    def test_read_user_by_username(self, sqlite_session: Session) -> None:
        sqlite_session.add(
            User(username="bob", email="bob@example.com", password_hash="x", role="viewer")
        )
        sqlite_session.commit()
        fetched = sqlite_session.query(User).filter_by(username="bob").first()
        assert fetched is not None
        assert fetched.email == "bob@example.com"

    def test_update_user_role(self, sqlite_session: Session) -> None:
        user = User(username="carol", email="carol@example.com", password_hash="x", role="viewer")
        sqlite_session.add(user)
        sqlite_session.commit()
        user.role = "admin"
        sqlite_session.commit()
        refreshed = sqlite_session.query(User).filter_by(username="carol").first()
        assert refreshed.role == "admin"

    def test_delete_user(self, sqlite_session: Session) -> None:
        user = User(username="dave", email="dave@example.com", password_hash="x", role="viewer")
        sqlite_session.add(user)
        sqlite_session.commit()
        sqlite_session.delete(user)
        sqlite_session.commit()
        gone = sqlite_session.query(User).filter_by(username="dave").first()
        assert gone is None

    def test_duplicate_username_raises(self, sqlite_session: Session) -> None:
        sqlite_session.add(
            User(username="dup", email="dup1@example.com", password_hash="x")
        )
        sqlite_session.commit()
        sqlite_session.add(
            User(username="dup", email="dup2@example.com", password_hash="x")
        )
        with pytest.raises(Exception):
            sqlite_session.commit()

    def test_is_active_default_true(self, sqlite_session: Session) -> None:
        user = User(username="active", email="a@b.com", password_hash="x")
        sqlite_session.add(user)
        sqlite_session.commit()
        assert user.is_active is True

    def test_repr(self, sqlite_session: Session) -> None:
        user = User(username="repr_user", email="r@r.com", password_hash="x", role="viewer")
        sqlite_session.add(user)
        sqlite_session.commit()
        assert "repr_user" in repr(user)


class TestPostModel:
    """CRUD tests for the Post ORM model."""

    @pytest.fixture
    def user(self, sqlite_session: Session) -> User:
        u = User(username="poster", email="poster@x.com", password_hash="x")
        sqlite_session.add(u)
        sqlite_session.commit()
        return u

    def test_create_post(self, sqlite_session: Session, user: User) -> None:
        post = Post(title="Hello World", user_id=user.id)
        sqlite_session.add(post)
        sqlite_session.commit()
        assert post.id is not None

    def test_post_belongs_to_user(self, sqlite_session: Session, user: User) -> None:
        post = Post(title="My Post", user_id=user.id)
        sqlite_session.add(post)
        sqlite_session.commit()
        fetched = sqlite_session.get(Post, post.id)
        assert fetched.user_id == user.id

    def test_delete_user_cascades_to_posts(
        self, sqlite_session: Session, user: User
    ) -> None:
        post = Post(title="Cascade Post", user_id=user.id)
        sqlite_session.add(post)
        sqlite_session.commit()
        post_id = post.id

        sqlite_session.delete(user)
        sqlite_session.commit()

        gone = sqlite_session.get(Post, post_id)
        assert gone is None

    def test_post_repr(self, sqlite_session: Session, user: User) -> None:
        post = Post(title="Repr Test", user_id=user.id)
        sqlite_session.add(post)
        sqlite_session.commit()
        assert "Repr Test" in repr(post)


class TestUserCredentialModel:
    """CRUD tests for the UserCredential ORM model."""

    @pytest.fixture
    def user(self, sqlite_session: Session) -> User:
        u = User(username="cred_owner", email="cred@x.com", password_hash="x")
        sqlite_session.add(u)
        sqlite_session.commit()
        return u

    def test_create_credential(self, sqlite_session: Session, user: User) -> None:
        cred = UserCredential(
            user_id=user.id,
            service="fabric",
            key="tenant_id",
            value="test-tenant-guid",
        )
        sqlite_session.add(cred)
        sqlite_session.commit()
        assert cred.id is not None

    def test_read_credential_by_service_key(
        self, sqlite_session: Session, user: User
    ) -> None:
        sqlite_session.add(
            UserCredential(
                user_id=user.id, service="snowflake", key="account", value="acc123"
            )
        )
        sqlite_session.commit()
        fetched = (
            sqlite_session.query(UserCredential)
            .filter_by(user_id=user.id, service="snowflake", key="account")
            .first()
        )
        assert fetched is not None
        assert fetched.value == "acc123"

    def test_update_credential_value(
        self, sqlite_session: Session, user: User
    ) -> None:
        cred = UserCredential(
            user_id=user.id, service="fabric", key="client_id", value="old"
        )
        sqlite_session.add(cred)
        sqlite_session.commit()
        cred.value = "new-client-id"
        sqlite_session.commit()
        refreshed = sqlite_session.get(UserCredential, cred.id)
        assert refreshed.value == "new-client-id"

    def test_delete_user_cascades_credentials(
        self, sqlite_session: Session, user: User
    ) -> None:
        cred = UserCredential(
            user_id=user.id, service="s3", key="token", value="tok"
        )
        sqlite_session.add(cred)
        sqlite_session.commit()
        cred_id = cred.id

        sqlite_session.delete(user)
        sqlite_session.commit()

        gone = sqlite_session.get(UserCredential, cred_id)
        assert gone is None

    def test_multiple_services_per_user(
        self, sqlite_session: Session, user: User
    ) -> None:
        for svc, key, val in [
            ("fabric", "tenant_id", "t1"),
            ("fabric", "client_id", "c1"),
            ("snowflake", "account", "sa1"),
        ]:
            sqlite_session.add(
                UserCredential(user_id=user.id, service=svc, key=key, value=val)
            )
        sqlite_session.commit()

        all_creds = (
            sqlite_session.query(UserCredential).filter_by(user_id=user.id).all()
        )
        assert len(all_creds) == 3
        services = {c.service for c in all_creds}
        assert services == {"fabric", "snowflake"}

    def test_cred_repr(self, sqlite_session: Session, user: User) -> None:
        cred = UserCredential(
            user_id=user.id, service="github", key="pat", value="gh_tok"
        )
        sqlite_session.add(cred)
        sqlite_session.commit()
        assert "github" in repr(cred)


class TestModelVersionHistoryModel:
    """CRUD tests for the ModelVersionHistory ORM model."""

    def test_create_version(self, sqlite_session: Session) -> None:
        mvh = ModelVersionHistory(
            model_name="SalesModel",
            version_tag="v1.0",
            yaml_hash="a" * 64,
        )
        sqlite_session.add(mvh)
        sqlite_session.commit()
        assert mvh.id is not None

    def test_read_version_by_model_name(self, sqlite_session: Session) -> None:
        sqlite_session.add(
            ModelVersionHistory(
                model_name="FinanceModel", version_tag="v2.0", yaml_hash="b" * 64
            )
        )
        sqlite_session.commit()
        fetched = (
            sqlite_session.query(ModelVersionHistory)
            .filter_by(model_name="FinanceModel")
            .first()
        )
        assert fetched is not None
        assert fetched.version_tag == "v2.0"

    def test_multiple_versions_same_model(self, sqlite_session: Session) -> None:
        for i, tag in enumerate(["v1.0", "v1.1", "v2.0"]):
            sqlite_session.add(
                ModelVersionHistory(
                    model_name="SharedModel",
                    version_tag=tag,
                    yaml_hash=f"{i:064d}",
                )
            )
        sqlite_session.commit()
        versions = (
            sqlite_session.query(ModelVersionHistory)
            .filter_by(model_name="SharedModel")
            .all()
        )
        assert len(versions) == 3
        tags = {v.version_tag for v in versions}
        assert tags == {"v1.0", "v1.1", "v2.0"}

    def test_delete_version(self, sqlite_session: Session) -> None:
        mvh = ModelVersionHistory(
            model_name="TempModel", version_tag="v0.1", yaml_hash="c" * 64
        )
        sqlite_session.add(mvh)
        sqlite_session.commit()
        sqlite_session.delete(mvh)
        sqlite_session.commit()
        assert sqlite_session.get(ModelVersionHistory, mvh.id) is None

    def test_repr(self, sqlite_session: Session) -> None:
        mvh = ModelVersionHistory(
            model_name="ReprModel", version_tag="v3.0", yaml_hash="d" * 64
        )
        sqlite_session.add(mvh)
        sqlite_session.commit()
        r = repr(mvh)
        assert "ReprModel" in r
        assert "v3.0" in r


# ---------------------------------------------------------------------------
# Session Factory Tests
# ---------------------------------------------------------------------------

class TestSessionFactory:
    """Tests for get_engine / get_session_factory / reset_engine."""

    @pytest.fixture(autouse=True)
    def reset_between_tests(self) -> Generator[None, None, None]:
        """Ensure singleton cache is cleared before/after each test."""
        reset_engine()
        yield
        reset_engine()

    def test_get_engine_with_sqlite(self) -> None:
        """get_engine should return a SQLAlchemy Engine for SQLite URL."""
        from sqlalchemy import Engine

        engine = get_engine(url_override="sqlite://")
        assert engine is not None
        assert engine.dialect.name == "sqlite"
        engine.dispose()

    def test_get_engine_is_singleton(self) -> None:
        """Same url_override should return the identical Engine object."""
        e1 = get_engine(url_override="sqlite://")
        e2 = get_engine(url_override="sqlite://")
        assert e1 is e2
        e1.dispose()

    def test_get_session_factory_returns_sessionmaker(self) -> None:
        """get_session_factory should return a callable sessionmaker."""
        factory = get_session_factory(url_override="sqlite://")
        assert callable(factory)
        with factory() as session:
            assert isinstance(session, Session)

    def test_reset_engine_clears_cache(self) -> None:
        """reset_engine should force a new Engine on next call."""
        e1 = get_engine(url_override="sqlite://")
        reset_engine()
        e2 = get_engine(url_override="sqlite://")
        # After reset the *id* will differ — new object
        assert e1 is not e2
        e2.dispose()

    def test_create_tables_via_engine(self) -> None:
        """Tables defined via Base.metadata should be creatable on SQLite."""
        engine = get_engine(url_override="sqlite://")
        Base.metadata.create_all(engine)
        inspector = inspect(engine)
        table_names = inspector.get_table_names()
        assert "users" in table_names
        assert "posts" in table_names
        assert "user_credentials" in table_names
        assert "model_version_history" in table_names
        engine.dispose()

    def test_session_factory_create_and_query_user(self) -> None:
        """Full round-trip: create user via sessionmaker, query it back."""
        from sqlalchemy.pool import StaticPool

        # StaticPool forces every connection to reuse the same underlying
        # sqlite3 connection, so the tables created by create_all() are
        # visible to all sessions attached to this engine.
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

        with SessionLocal() as session:
            session.add(
                User(
                    username="factory_user",
                    email="fu@example.com",
                    password_hash="hashed",
                    role="viewer",
                )
            )
            session.commit()

        with SessionLocal() as session:
            user = session.query(User).filter_by(username="factory_user").first()
            assert user is not None
            assert user.email == "fu@example.com"

        engine.dispose()


# ---------------------------------------------------------------------------
# Factory Tests (create_repository_backend)
# ---------------------------------------------------------------------------

class TestFactory:
    """Test the create_repository_backend factory function."""

    def test_factory_creates_duckdb_by_default(self, tmp_db_path: str) -> None:
        """Factory should create DuckDB backend by default."""
        backend = create_repository_backend(db_path=tmp_db_path)
        assert isinstance(backend, DuckDBBackend)
        backend.close()

    def test_factory_creates_duckdb_with_explicit_type(self, tmp_db_path: str) -> None:
        """Factory should create DuckDB backend when type='duckdb'."""
        backend = create_repository_backend(
            backend_type="duckdb", db_path=tmp_db_path
        )
        assert isinstance(backend, DuckDBBackend)
        backend.close()

    def test_factory_creates_orm_with_sqlite_url(self) -> None:
        """Factory should create ORMBackend when given a connection_url."""
        backend = create_repository_backend(
            backend_type="orm", connection_url="sqlite://"
        )
        assert isinstance(backend, ORMBackend)
        backend.close()

    def test_factory_raises_for_unknown_type(self) -> None:
        """Factory should raise ValueError for unknown backend types."""
        with pytest.raises(ValueError, match="Unknown backend_type"):
            create_repository_backend(backend_type="mongodb")

    def test_factory_orm_requires_connection_url(self, monkeypatch) -> None:
        """Factory should raise ValueError if ORM backend has no URL."""
        # Temporarily clear URL env vars so the factory cannot resolve a URL
        monkeypatch.delenv("SEMABRIDGE_DATABASE_URL", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("SEMABRIDGE_DB_BACKEND", raising=False)
        # Clear the db_resolver cache so it re-reads the (now empty) env
        from semabridge.core.db_resolver import clear_db_config_cache
        clear_db_config_cache()
        with pytest.raises(ValueError, match="connection_url is required"):
            create_repository_backend(backend_type="orm")

    def test_factory_auto_resolves_duckdb_path(self) -> None:
        """Factory should auto-resolve DB path when none is provided."""
        backend = create_repository_backend(backend_type="duckdb")
        assert isinstance(backend, DuckDBBackend)
        backend.close()

    def test_factory_infers_duckdb_from_db_path(self, tmp_db_path: str) -> None:
        """When only db_path is given (no backend_type), should pick DuckDB."""
        backend = create_repository_backend(db_path=tmp_db_path)
        assert isinstance(backend, DuckDBBackend)
        backend.close()

    def test_factory_infers_orm_from_url(self) -> None:
        """When only connection_url is given (no backend_type), should pick ORM."""
        backend = create_repository_backend(connection_url="sqlite://")
        assert isinstance(backend, ORMBackend)
        backend.close()
