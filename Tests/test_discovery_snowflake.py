from __future__ import annotations

import os

from fastapi.testclient import TestClient

from semabridge.repository.orm.base import Base
from semabridge.repository.orm.session_factory import db_manager, reset_engine


def _prepare_db() -> None:
    os.environ['AUTH_ENABLED'] = 'false'
    os.environ['SEMABRIDGE_DATABASE_URL'] = 'sqlite:///file::memory:?cache=shared&uri=true'
    os.environ['SEMABRIDGE_DB_BACKEND'] = 'orm'

    reset_engine()
    db_manager.reset()
    engine = db_manager.get_engine()
    Base.metadata.create_all(bind=engine)


def test_discover_snowflake_returns_actionable_error_for_missing_identity_account():
    _prepare_db()

    from semabridge.api.main import app

    with TestClient(app) as client:
        response = client.get('/api/discovery/snowflake', params={'identity_id': '241347f2-70b3-4bb5-9586-85bc50b2bd7b'})

    assert response.status_code == 400
    assert "No Snowflake account found for identity_id '241347f2-70b3-4bb5-9586-85bc50b2bd7b'." in response.json()['detail']
    assert 'Settings -> Connections' in response.json()['detail']