from __future__ import annotations

import json
import os

from fastapi.testclient import TestClient
from sqlalchemy import select

from semabridge.api.main import app
from semabridge.auth.encryption import decrypt_token, encrypt_token
from semabridge.repository.orm.base import Base
from semabridge.repository.orm.models import Account
from semabridge.repository.orm.session_factory import db_manager, reset_engine


def _prepare_db() -> None:
    os.environ['AUTH_ENABLED'] = 'false'
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


def test_edit_snowflake_account_without_retyping_secrets_preserves_them():
    """Regression test: editing an existing Snowflake key-pair account without
    resubmitting private_key/private_key_passphrase (the UI never pre-fills
    secret fields on edit) must not erase them from the stored bundle — the
    new submission should be merged onto the prior bundle, not replace it."""
    _prepare_db()

    original_bundle = {
        'account': 'abc123',
        'user': 'svc_user',
        'warehouse': 'COMPUTE_WH',
        'database': 'OLD_DB',
        'auth_type': 'keypair',
        'private_key': '-----BEGIN PRIVATE KEY-----\nFAKEKEY\n-----END PRIVATE KEY-----',
        'private_key_passphrase': 'super-secret-passphrase',
    }

    with db_manager.get_session() as session:
        session.add(
            Account(
                id='acct-sf-1',
                connector_type='SNOWFLAKE',
                tag='sf1',
                identity_email='svc_user',
                encrypted_token=encrypt_token(json.dumps(original_bundle)),
                auth_type='keypair',
                status='Active',
                is_default=False,
                owner_id=None,
            )
        )
        session.commit()

    # Simulate the settings-form edit: user only changes `database`, and the
    # secret fields arrive blank (never retyped) so they're stripped from the
    # payload entirely, exactly like ConnectionsPanel.jsx's `filtered` dict.
    payload = {
        'connector_type': 'SNOWFLAKE',
        'tag': 'sf1',
        'identity_email': 'svc_user',
        'credentials': {
            'account': 'abc123',
            'user': 'svc_user',
            'warehouse': 'COMPUTE_WH',
            'database': 'NEW_DB',
            'auth_type': 'keypair',
        },
    }

    with TestClient(app) as client:
        response = client.post('/api/accounts', json=payload)

        with db_manager.get_session() as session:
            persisted = session.execute(select(Account).where(Account.tag == 'sf1')).scalars().one()

    assert response.status_code == 201
    bundle = json.loads(decrypt_token(persisted.encrypted_token))
    assert bundle['database'] == 'NEW_DB'
    assert bundle['private_key'] == original_bundle['private_key']
    assert bundle['private_key_passphrase'] == original_bundle['private_key_passphrase']


def test_edit_snowflake_account_switching_auth_mode_purges_stale_secrets():
    """Switching auth mode (password -> keypair) must not resurrect the old
    mode's secret via the merge — the merge should purge auth-exclusive keys
    before overlaying the new submission, matching CredentialManager's
    existing purge-on-auth-switch behaviour."""
    _prepare_db()

    original_bundle = {
        'account': 'abc123',
        'user': 'svc_user',
        'warehouse': 'COMPUTE_WH',
        'database': 'SEMABRIDGE',
        'auth_type': 'password',
        'password': 'old-password-should-not-survive',
    }

    with db_manager.get_session() as session:
        session.add(
            Account(
                id='acct-sf-2',
                connector_type='SNOWFLAKE',
                tag='sf2',
                identity_email='svc_user',
                encrypted_token=encrypt_token(json.dumps(original_bundle)),
                auth_type='password',
                status='Active',
                is_default=False,
                owner_id=None,
            )
        )
        session.commit()

    payload = {
        'connector_type': 'SNOWFLAKE',
        'tag': 'sf2',
        'identity_email': 'svc_user',
        'credentials': {
            'account': 'abc123',
            'user': 'svc_user',
            'warehouse': 'COMPUTE_WH',
            'database': 'SEMABRIDGE',
            'auth_type': 'keypair',
            'private_key': '-----BEGIN PRIVATE KEY-----\nNEWKEY\n-----END PRIVATE KEY-----',
            'private_key_passphrase': 'new-passphrase',
        },
    }

    with TestClient(app) as client:
        response = client.post('/api/accounts', json=payload)

        with db_manager.get_session() as session:
            persisted = session.execute(select(Account).where(Account.tag == 'sf2')).scalars().one()

    assert response.status_code == 201
    bundle = json.loads(decrypt_token(persisted.encrypted_token))
    assert bundle['auth_type'] == 'keypair'
    assert bundle['private_key_passphrase'] == 'new-passphrase'
    assert 'password' not in bundle
