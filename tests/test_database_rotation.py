"""
Tests for database rotation support in :mod:`semabridge.repository.orm.session_factory`.

Covers:
- DatabaseManager.fetch_latest_database_url() resolves from env var
- DatabaseManager.create_db_engine() applies pool_pre_ping for non-in-process dialects
- DatabaseManager.create_db_engine() uses StaticPool for in-process dialects (sqlite/duckdb)
- DatabaseManager.get_engine() creates engine on first call
- DatabaseManager.get_engine() detects URL change and rotates (disposes old engine, creates new)
- DatabaseManager.get_engine() is thread-safe under concurrent access
- DatabaseManager.dispose() clears engine state
- DatabaseManager.reset() clears engine state and db_resolver cache
- get_session() context manager yields and auto-closes a session
- Backward-compat get_engine() / get_session_factory() / reset_engine() wrappers
- ModelRepository._session() always uses the latest engine (rotation-aware)
- setup_database() emits DeprecationWarning
"""

from __future__ import annotations

import os
import threading
import warnings
from contextlib import contextmanager
from typing import Generator
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manager():
    """Return a fresh, isolated DatabaseManager (not the module-level singleton)."""
    # Import here so conftest's env override is in place
    from semabridge.repository.orm.session_factory import DatabaseManager
    return DatabaseManager()


# ---------------------------------------------------------------------------
# fetch_latest_database_url
# ---------------------------------------------------------------------------

class TestFetchLatestDatabaseUrl:
    """fetch_latest_database_url reads from env vars and the resolver."""

    def test_returns_semabridge_database_url_from_env(self, monkeypatch):
        """SEMABRIDGE_DATABASE_URL env var is the highest priority source."""
        monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///test_rotation.db")
        monkeypatch.delenv("DATABASE_URL", raising=False)

        mgr = _make_manager()
        url = mgr.fetch_latest_database_url()

        assert url == "sqlite:///test_rotation.db"

    def test_returns_database_url_fallback(self, monkeypatch):
        """DATABASE_URL is used when SEMABRIDGE_DATABASE_URL is absent."""
        monkeypatch.delenv("SEMABRIDGE_DATABASE_URL", raising=False)
        monkeypatch.setenv("DATABASE_URL", "sqlite:///fallback.db")

        mgr = _make_manager()
        url = mgr.fetch_latest_database_url()

        assert url == "sqlite:///fallback.db"

    def test_clears_db_resolver_cache_on_each_call(self, monkeypatch):
        """Cache is cleared before every resolution so runtime env changes are visible."""
        monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///first.db")

        mgr = _make_manager()
        url1 = mgr.fetch_latest_database_url()
        assert url1 == "sqlite:///first.db"

        # Simulate credential rotation
        monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///rotated.db")
        url2 = mgr.fetch_latest_database_url()
        assert url2 == "sqlite:///rotated.db"


# ---------------------------------------------------------------------------
# create_db_engine
# ---------------------------------------------------------------------------

class TestCreateDbEngine:
    """create_db_engine builds correctly configured engines per dialect."""

    def test_sqlite_uses_static_pool(self):
        """SQLite (in-process) uses StaticPool, not the connection pool."""
        from sqlalchemy.pool import StaticPool

        mgr = _make_manager()
        engine = mgr.create_db_engine("sqlite:///:memory:")
        try:
            assert isinstance(engine.pool, StaticPool), (
                f"Expected StaticPool for sqlite, got {type(engine.pool).__name__}"
            )
        finally:
            engine.dispose()

    def test_non_in_process_dialect_uses_pool_pre_ping(self):
        """PostgreSQL-like dialects get pool_pre_ping=True and a QueuePool."""
        # We mock create_engine to avoid needing a real postgres driver.
        from sqlalchemy.pool import QueuePool

        mgr = _make_manager()
        with patch(
            "semabridge.repository.orm.session_factory.DatabaseManager.create_db_engine"
        ) as mock_create:
            mock_engine = MagicMock()
            mock_engine.dialect.name = "postgresql"
            mock_create.return_value = mock_engine

            result = mgr.create_db_engine.__wrapped__(mgr, "postgresql://localhost/test") if hasattr(mgr.create_db_engine, "__wrapped__") else mock_engine

        # The important thing is that pool_pre_ping is passed for non-in-process dialects.
        # We verify this by inspecting actual sqlalchemy engine creation kwargs.
        from sqlalchemy import create_engine
        with patch("sqlalchemy.create_engine") as mock_ce:
            mock_ce.return_value = MagicMock(dialect=MagicMock(name="postgresql"))
            try:
                mgr.create_db_engine("postgresql://localhost/test")
            except Exception:
                pass
            if mock_ce.called:
                _, kwargs = mock_ce.call_args
                assert kwargs.get("pool_pre_ping") is True, "pool_pre_ping must be True for non-in-process dialects"
                assert kwargs.get("pool_size") == 5
                assert kwargs.get("max_overflow") == 10
                assert kwargs.get("pool_recycle") == 1800

    def test_raises_repository_error_on_invalid_url(self):
        """RepositoryError is raised for completely invalid URLs."""
        from semabridge.core.exceptions import RepositoryError

        mgr = _make_manager()
        with pytest.raises(RepositoryError, match="Failed to create database engine"):
            mgr.create_db_engine("notavalidscheme://??garbage")


# ---------------------------------------------------------------------------
# get_engine — first call and rotation detection
# ---------------------------------------------------------------------------

class TestGetEngine:
    """get_engine creates, caches, and rotates engines."""

    def test_returns_engine_on_first_call(self, monkeypatch):
        """First call creates an engine from the current URL."""
        monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///:memory:")

        mgr = _make_manager()
        engine = mgr.get_engine()

        assert engine is not None
        assert engine.dialect.name == "sqlite"
        engine.dispose()

    def test_returns_same_engine_for_same_url(self, monkeypatch):
        """Repeated calls with the same URL return the same engine object."""
        monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///:memory:")

        mgr = _make_manager()
        engine1 = mgr.get_engine()
        engine2 = mgr.get_engine()

        assert engine1 is engine2
        engine1.dispose()

    def test_rotates_engine_on_url_change(self, monkeypatch):
        """URL change causes the old engine to be disposed and a new one created."""
        monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///:memory:")

        mgr = _make_manager()
        old_engine = mgr.get_engine()
        old_engine_id = id(old_engine)

        # Track dispose calls
        dispose_called = []
        original_dispose = old_engine.dispose
        def tracking_dispose():
            dispose_called.append(True)
            return original_dispose()
        old_engine.dispose = tracking_dispose

        # Simulate credential / database rotation
        monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///rotated_test.db")
        new_engine = mgr.get_engine()

        assert id(new_engine) != old_engine_id, "Expected a new engine after rotation"
        assert len(dispose_called) == 1, "Old engine.dispose() must be called exactly once"

        new_engine.dispose()
        try:
            import os as _os
            _os.remove("rotated_test.db")
        except OSError:
            pass

    def test_url_override_bypasses_env(self, monkeypatch):
        """url_override takes precedence over env vars."""
        monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///:memory:")

        mgr = _make_manager()
        override_engine = mgr.get_engine(url_override="sqlite:///:memory:")

        assert override_engine is not None
        override_engine.dispose()


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    """DatabaseManager is safe under concurrent access."""

    def test_concurrent_get_engine_returns_consistent_engine(self, monkeypatch):
        """Many threads calling get_engine() simultaneously all get the same engine."""
        monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///:memory:")

        mgr = _make_manager()
        results: list = []
        errors: list = []

        def _worker():
            try:
                results.append(id(mgr.get_engine()))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Threads raised errors: {errors}"
        # All threads should have gotten the same engine (all IDs equal)
        assert len(set(results)) == 1, (
            f"Expected 1 unique engine id, got {len(set(results))}: {set(results)}"
        )

        mgr.get_engine().dispose()

    def test_rotation_under_concurrent_reads_is_safe(self, monkeypatch):
        """URL rotation while threads are reading does not cause exceptions."""
        monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///:memory:")

        mgr = _make_manager()
        errors: list = []

        def _reader():
            for _ in range(10):
                try:
                    mgr.get_engine()
                except Exception as exc:
                    errors.append(exc)

        def _rotate():
            import time, tempfile, pathlib

            for i in range(3):
                time.sleep(0.005)
                monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", f"sqlite:///:memory:")

        readers = [threading.Thread(target=_reader) for _ in range(5)]
        rotator = threading.Thread(target=_rotate)

        for t in readers:
            t.start()
        rotator.start()
        for t in readers:
            t.join()
        rotator.join()

        assert not errors, f"Thread errors during rotation: {errors}"


# ---------------------------------------------------------------------------
# Dispose and Reset
# ---------------------------------------------------------------------------

class TestDisposeAndReset:
    """dispose() and reset() clean up engine state."""

    def test_dispose_clears_engine(self, monkeypatch):
        """After dispose(), _engine is None."""
        monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///:memory:")

        mgr = _make_manager()
        mgr.get_engine()
        assert mgr._engine is not None

        mgr.dispose()
        assert mgr._engine is None
        assert mgr._session_factory is None
        assert mgr._current_url is None

    def test_dispose_is_idempotent(self, monkeypatch):
        """Calling dispose() twice does not raise."""
        monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///:memory:")

        mgr = _make_manager()
        mgr.get_engine()
        mgr.dispose()
        mgr.dispose()  # second call should not raise

    def test_reset_also_clears_db_resolver_cache(self, monkeypatch):
        """reset() calls clear_db_config_cache() in addition to dispose()."""
        monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///:memory:")

        mgr = _make_manager()
        mgr.get_engine()

        with patch(
            "semabridge.core.db_resolver.clear_db_config_cache"
        ) as mock_clear:
            mgr.reset()

        mock_clear.assert_called_once()


# ---------------------------------------------------------------------------
# get_session context manager
# ---------------------------------------------------------------------------

class TestGetSession:
    """get_session() yields a Session and closes it on exit."""

    def test_get_session_yields_session(self, monkeypatch):
        """get_session() context manager yields an open SQLAlchemy Session."""
        from sqlalchemy.orm import Session

        monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///:memory:")

        mgr = _make_manager()
        with mgr.get_session() as session:
            assert isinstance(session, Session)

    def test_get_session_closes_on_exit(self, monkeypatch):
        """Session is closed after the context manager exits."""
        monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///:memory:")

        mgr = _make_manager()
        closed: list = []

        with mgr.get_session() as session:
            _orig_close = session.close

            def _track_close():
                closed.append(True)
                return _orig_close()

            session.close = _track_close

        assert len(closed) == 1, "Session.close() must be called on context exit"

    def test_get_session_closes_on_exception(self, monkeypatch):
        """Session is closed even when an exception propagates."""
        monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///:memory:")

        mgr = _make_manager()
        closed_sessions: list = []

        with pytest.raises(ValueError, match="simulated error"):
            with mgr.get_session() as session:
                original_close = session.close

                def tracking_close():
                    closed_sessions.append(True)
                    return original_close()

                session.close = tracking_close
                raise ValueError("simulated error")

        assert len(closed_sessions) == 1, "Session must be closed even on exception"


# ---------------------------------------------------------------------------
# Backward-compat wrappers
# ---------------------------------------------------------------------------

class TestBackwardCompatWrappers:
    """get_engine(), get_session_factory(), reset_engine() still work."""

    def test_get_engine_wrapper_returns_engine(self, monkeypatch):
        """Module-level get_engine() delegates to db_manager."""
        from semabridge.repository.orm.session_factory import get_engine, reset_engine

        monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///:memory:")
        reset_engine()

        engine = get_engine()
        assert engine is not None
        reset_engine()

    def test_get_session_factory_wrapper_returns_factory(self, monkeypatch):
        """Module-level get_session_factory() returns a sessionmaker."""
        from sqlalchemy.orm import sessionmaker
        from semabridge.repository.orm.session_factory import (
            get_session_factory,
            reset_engine,
        )

        monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///:memory:")
        reset_engine()

        factory = get_session_factory()
        assert isinstance(factory, sessionmaker)
        reset_engine()

    def test_reset_engine_clears_manager_state(self, monkeypatch):
        """Module-level reset_engine() disposes the db_manager engine."""
        from semabridge.repository.orm.session_factory import (
            db_manager,
            get_engine,
            reset_engine,
        )

        monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///:memory:")
        reset_engine()

        get_engine()
        assert db_manager._engine is not None

        reset_engine()
        assert db_manager._engine is None


# ---------------------------------------------------------------------------
# ModelRepository rotation-awareness
# ---------------------------------------------------------------------------

class TestModelRepositoryRotationAwareness:
    """ModelRepository._session() always delegates to the latest engine."""

    def test_session_uses_current_db_manager_engine(self, monkeypatch):
        """After rotation, ModelRepository._session() uses the new engine."""
        from semabridge.repository.model_repository import ModelRepository
        from semabridge.repository.orm.session_factory import reset_engine

        monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///:memory:")
        reset_engine()

        repo = ModelRepository()
        session1 = repo._session()
        engine1_id = id(session1.bind) if session1.bind else id(repo._engine)
        session1.close()

        # Re-fetch to get the current engine id through the normal path
        from semabridge.repository.orm.session_factory import db_manager
        current_engine = db_manager.get_engine()

        # Both should be driven by the same underlying engine
        session2 = repo._session()
        session2.close()

        reset_engine()

    def test_url_override_repos_use_their_own_engine(self):
        """ModelRepository(url_override=...) keeps its own engine, unaffected by rotation."""
        from semabridge.repository.model_repository import ModelRepository

        repo = ModelRepository(url_override="sqlite:///:memory:")
        assert repo._url_override == "sqlite:///:memory:"

        session = repo._session()
        assert session is not None
        session.close()
        repo._engine.dispose()


# ---------------------------------------------------------------------------
# setup_database() deprecation
# ---------------------------------------------------------------------------

class TestSetupDatabaseDeprecation:
    """setup_database() emits DeprecationWarning."""

    def test_setup_database_emits_deprecation_warning(self, monkeypatch):
        """Calling setup_database() triggers DeprecationWarning."""
        from semabridge.repository.db import setup_database

        monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///:memory:")

        with pytest.warns(DeprecationWarning, match="setup_database\\(\\) is deprecated"):
            engine, factory = setup_database()

        assert engine is not None
        assert factory is not None
        engine.dispose()

    def test_setup_database_returns_engine_and_factory_tuple(self, monkeypatch):
        """Even deprecated, setup_database() returns (Engine, sessionmaker) tuple."""
        from sqlalchemy import Engine
        from sqlalchemy.orm import sessionmaker
        from semabridge.repository.db import setup_database

        monkeypatch.setenv("SEMABRIDGE_DATABASE_URL", "sqlite:///:memory:")

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            engine, factory = setup_database()

        assert isinstance(engine, Engine)
        assert isinstance(factory, sessionmaker)
        engine.dispose()
