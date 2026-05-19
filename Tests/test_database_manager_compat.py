from sqlalchemy import text

from semabridge.repository.orm.session_factory import DatabaseManager


def test_legacy_session_method_returns_usable_session():
    manager = DatabaseManager()
    session = manager._session(url_override="sqlite://")

    try:
        assert session.execute(text("select 1")).scalar_one() == 1
    finally:
        session.close()
        manager.dispose()
