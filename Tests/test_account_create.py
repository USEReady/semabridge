from __future__ import annotations

import os

from fastapi.testclient import TestClient
from sqlalchemy import select

from semabridge.api.main import app
from semabridge.auth.encryption import decrypt_token
from semabridge.repository.orm.base import Base
from semabridge.repository.orm.models import Account
from semabridge.repository.orm.session_factory import db_manager, reset_engine


def _prepare_db() -> None:
    os.environ.pop('AUTH_ENABLED', None)
    os.environ['SEMABRIDGE_DATABASE_URL'] = 'sqlite:///file::memory:?cache=shared&uri=true'
    os.environ['SEMABRIDGE_DB_BACKEND'] = 'orm'

    reset_engine()
    db_manager.reset()
    engine = db_manager.get_engine()
    Base.metadata.create_all(bind=engine)


def test_create_account_is_idempotent_for_same_tag_and_connector():
    _prepare_db()

    with db_manager.get_session() as session:
        session.add(
            Account(
                id='acct-fabric-1',
                connector_type='FABRIC',
                tag='cc',
                identity_email='owner@example.com',
                status='Active',
                is_default=False,
                owner_id=None,
            )
        )
        session.commit()

    payload = {
        'connector_type': 'FABRIC',
        'tag': 'cc',
        'identity_email': 'owner@example.com',
    }

    with TestClient(app) as client:
        response = client.post('/api/accounts', json=payload)

        with db_manager.get_session() as session:
            persisted = session.execute(select(Account).where(Account.tag == 'cc')).scalars().all()

    assert response.status_code == 201
    accounts = response.json()
    assert len(accounts) == 1
    assert accounts[0]['tag'] == 'cc'
    assert accounts[0]['connector_type'] == 'FABRIC'

    assert len(persisted) == 1


def test_create_account_refreshes_existing_fabric_token_for_same_tag():
    _prepare_db()

    with db_manager.get_session() as session:
        session.add(
            Account(
                id='acct-fabric-1',
                connector_type='FABRIC',
                tag='cc',
                identity_email='owner@example.com',
                encrypted_token='legacy-token',
                status='Active',
                is_default=False,
                owner_id=None,
            )
        )
        session.commit()

    payload = {
        'connector_type': 'FABRIC',
        'tag': 'cc',
        'identity_email': 'owner@example.com',
        'encrypted_token': 'fresh-token',
    }

    with TestClient(app) as client:
        response = client.post('/api/accounts', json=payload)

        with db_manager.get_session() as session:
            persisted = session.execute(select(Account).where(Account.tag == 'cc')).scalars().all()

    assert response.status_code == 201
    assert len(persisted) == 1
    assert decrypt_token(persisted[0].encrypted_token) == 'fresh-token'


def test_create_account_rejects_tag_reuse_across_connector_types():
    _prepare_db()

    with db_manager.get_session() as session:
        session.add(
            Account(
                id='acct-fabric-2',
                connector_type='FABRIC',
                tag='cc',
                identity_email='owner@example.com',
                status='Active',
                is_default=False,
                owner_id=None,
            )
        )
        session.commit()

    payload = {
        'connector_type': 'SNOWFLAKE',
        'tag': 'cc',
        'identity_email': 'owner@example.com',
    }

    with TestClient(app) as client:
        response = client.post('/api/accounts', json=payload)

    assert response.status_code == 409
    assert "already used by connector 'FABRIC'" in response.json()['detail']
