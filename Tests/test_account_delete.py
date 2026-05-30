from __future__ import annotations

import os

from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text

from semabridge.api.app_setup import _apply_schema_compatibility_fixes
from semabridge.api.main import app
from semabridge.repository.orm.base import Base
from semabridge.repository.orm.models import Account, Project
from semabridge.repository.orm.session_factory import db_manager, reset_engine


def _prepare_legacy_schema(engine):
    with engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE IF EXISTS projects')
        conn.exec_driver_sql('DROP TABLE IF EXISTS accounts')
        conn.exec_driver_sql(
            '''
            CREATE TABLE accounts (
                id VARCHAR(36) PRIMARY KEY,
                connector_type VARCHAR(50) NOT NULL,
                tag VARCHAR(255) NOT NULL,
                identity_email VARCHAR(255),
                encrypted_token TEXT,
                status VARCHAR(50) NOT NULL,
                is_default BOOLEAN NOT NULL DEFAULT 0
            )
            '''
        )
        conn.exec_driver_sql(
            '''
            CREATE TABLE projects (
                project_id VARCHAR(36) PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                workspace_id VARCHAR(255),
                warehouse VARCHAR(255),
                database VARCHAR(255),
                schema VARCHAR(255),
                adapter VARCHAR(50),
                source_connection TEXT,
                last_updated TIMESTAMP,
                connection_tag VARCHAR(255)
            )
            '''
        )


def _prepare_projects_missing_connection_tag_schema(engine):
    with engine.begin() as conn:
        conn.exec_driver_sql('DROP TABLE IF EXISTS projects')
        conn.exec_driver_sql('DROP TABLE IF EXISTS accounts')
        conn.exec_driver_sql(
            '''
            CREATE TABLE accounts (
                id VARCHAR(36) PRIMARY KEY,
                connector_type VARCHAR(50) NOT NULL,
                tag VARCHAR(255) NOT NULL,
                identity_email VARCHAR(255),
                encrypted_token TEXT,
                status VARCHAR(50) NOT NULL,
                is_default BOOLEAN NOT NULL DEFAULT 0
            )
            '''
        )
        conn.exec_driver_sql(
            '''
            CREATE TABLE projects (
                project_id VARCHAR(36) PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                workspace_id VARCHAR(255),
                warehouse VARCHAR(255),
                database VARCHAR(255),
                schema VARCHAR(255),
                adapter VARCHAR(50),
                source_connection TEXT,
                last_updated TIMESTAMP,
                account_id VARCHAR(36)
            )
            '''
        )


def test_delete_account_detaches_linked_projects():
    os.environ['AUTH_ENABLED'] = 'false'
    os.environ['SEMABRIDGE_DATABASE_URL'] = 'sqlite:///file::memory:?cache=shared&uri=true'
    os.environ['SEMABRIDGE_DB_BACKEND'] = 'orm'

    reset_engine()
    db_manager.reset()
    engine = db_manager.get_engine()
    Base.metadata.create_all(bind=engine)

    with db_manager.get_session() as session:
        account = Account(
            id='acct-1',
            connector_type='FABRIC',
            tag='acct-1',
            identity_email='owner@example.com',
            status='Active',
            is_default=False,
            owner_id=None,
        )
        project = Project(
            project_id='proj-1',
            name='Project One',
            account_id='acct-1',
        )
        session.add(account)
        session.add(project)
        session.commit()

    with TestClient(app) as client:
        response = client.delete('/api/accounts/acct-1')

        assert response.status_code == 200
        assert response.json() == []

        with db_manager.get_session() as session:
            remaining_account = session.execute(select(Account).where(Account.id == 'acct-1')).scalar_one_or_none()
            remaining_project = session.execute(select(Project).where(Project.project_id == 'proj-1')).scalar_one_or_none()

        assert remaining_account is None
        assert remaining_project is not None
        assert remaining_project.account_id is None


def test_delete_account_repairs_legacy_projects_schema():
    os.environ['AUTH_ENABLED'] = 'false'
    os.environ['SEMABRIDGE_DATABASE_URL'] = 'sqlite:///file::memory:?cache=shared&uri=true'
    os.environ['SEMABRIDGE_DB_BACKEND'] = 'orm'

    reset_engine()
    db_manager.reset()
    engine = db_manager.get_engine()
    _prepare_legacy_schema(engine)

    with db_manager.get_session() as session:
        session.execute(
            text(
                "INSERT INTO accounts (id, connector_type, tag, identity_email, encrypted_token, status, is_default) "
                "VALUES (:id, :connector_type, :tag, :identity_email, :encrypted_token, :status, :is_default)"
            ),
            {
                'id': 'acct-legacy',
                'connector_type': 'FABRIC',
                'tag': 'acct-legacy',
                'identity_email': 'owner@example.com',
                'encrypted_token': None,
                'status': 'Active',
                'is_default': False,
            },
        )
        session.commit()

    with TestClient(app) as client:
        response = client.delete('/api/accounts/acct-legacy')

        assert response.status_code == 200
        assert response.json() == []

        columns = {col['name'] for col in inspect(engine).get_columns('projects')}
        assert 'account_id' in columns


def test_delete_account_with_projects_missing_connection_tag_column():
    os.environ['AUTH_ENABLED'] = 'false'
    os.environ['SEMABRIDGE_DATABASE_URL'] = 'sqlite:///file::memory:?cache=shared&uri=true'
    os.environ['SEMABRIDGE_DB_BACKEND'] = 'orm'

    reset_engine()
    db_manager.reset()
    engine = db_manager.get_engine()
    _prepare_projects_missing_connection_tag_schema(engine)

    with db_manager.get_session() as session:
        session.execute(
            text(
                "INSERT INTO accounts (id, connector_type, tag, identity_email, encrypted_token, status, is_default) "
                "VALUES (:id, :connector_type, :tag, :identity_email, :encrypted_token, :status, :is_default)"
            ),
            {
                'id': 'acct-conn-tag',
                'connector_type': 'FABRIC',
                'tag': 'acct-conn-tag',
                'identity_email': 'owner@example.com',
                'encrypted_token': None,
                'status': 'Active',
                'is_default': False,
            },
        )
        session.execute(
            text(
                "INSERT INTO projects (project_id, name, account_id) "
                "VALUES (:project_id, :name, :account_id)"
            ),
            {
                'project_id': 'proj-conn-tag',
                'name': 'Legacy Project',
                'account_id': 'acct-conn-tag',
            },
        )
        session.commit()

    with TestClient(app) as client:
        response = client.delete('/api/accounts/acct-conn-tag')

        assert response.status_code == 200
        assert response.json() == []

        with db_manager.get_session() as session:
            remaining_account = session.execute(select(Account).where(Account.id == 'acct-conn-tag')).scalar_one_or_none()
            linked_account_id = session.execute(
                text("SELECT account_id FROM projects WHERE project_id = 'proj-conn-tag'")
            ).scalar_one_or_none()

        assert remaining_account is None
        assert linked_account_id is None


def test_schema_compatibility_adds_missing_projects_connection_tag_column():
    os.environ['AUTH_ENABLED'] = 'false'
    os.environ['SEMABRIDGE_DATABASE_URL'] = 'sqlite:///file::memory:?cache=shared&uri=true'
    os.environ['SEMABRIDGE_DB_BACKEND'] = 'orm'

    reset_engine()
    db_manager.reset()
    engine = db_manager.get_engine()
    _prepare_projects_missing_connection_tag_schema(engine)

    _apply_schema_compatibility_fixes()

    columns = {col['name'] for col in inspect(engine).get_columns('projects')}
    assert 'connection_tag' in columns
