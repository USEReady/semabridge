from __future__ import annotations

import pytest
import aiohttp
from unittest.mock import AsyncMock, MagicMock
from semabridge.notifications.adapters.slack_adapter import SlackAdapter
from semabridge.notifications.adapters.teams_adapter import TeamsAdapter
from semabridge.notifications.adapters.webhook_adapter import WebhookAdapter
from semabridge.notifications.adapters.pagerduty_adapter import PagerDutyAdapter


@pytest.mark.asyncio
async def test_slack_adapter_session_lifecycle(monkeypatch):
    adapter = SlackAdapter({"webhook_url": "https://hooks.slack.com/services/test/url"})
    
    # Verify session starts as None
    assert adapter._session is None
    
    # Mock post call as an async context manager returning mock_response
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.text = AsyncMock(return_value="ok")
    
    mock_post = MagicMock()
    mock_post.return_value.__aenter__ = AsyncMock(return_value=mock_response)
    mock_post.return_value.__aexit__ = AsyncMock(return_value=None)
    
    # We patch aiohttp.ClientSession.post
    monkeypatch.setattr(aiohttp.ClientSession, "post", mock_post)
    
    result = await adapter.send({"text": "hello"}, adapter.config)
    assert result["success"] is True
    assert result["response_code"] == 200
    
    # Check that session is instantiated and open
    session = adapter._session
    assert session is not None
    assert not session.closed
    
    # Close session
    await adapter.close()
    assert session.closed
    
    # Send again should recreate session
    result = await adapter.send({"text": "hello again"}, adapter.config)
    assert result["success"] is True
    assert adapter._session is not None
    assert adapter._session is not session  # should be a new session
    assert not adapter._session.closed
    
    await adapter.close()


@pytest.mark.asyncio
async def test_teams_adapter_test_connection(monkeypatch):
    adapter = TeamsAdapter({"webhook_url": "https://outlook.office.com/webhook/test"})
    
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.text = AsyncMock(return_value="ok")
    
    mock_post = MagicMock()
    mock_post.return_value.__aenter__ = AsyncMock(return_value=mock_response)
    mock_post.return_value.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(aiohttp.ClientSession, "post", mock_post)
    
    success, error = await adapter.test_connection(adapter.config)
    assert success is True
    assert error == ""
    
    await adapter.close()


@pytest.mark.asyncio
async def test_webhook_adapter_send(monkeypatch):
    adapter = WebhookAdapter({"webhook_url": "https://example.com/webhook"})
    
    mock_response = MagicMock()
    mock_response.status = 201
    mock_response.text = AsyncMock(return_value="created")
    
    mock_post = MagicMock()
    mock_post.return_value.__aenter__ = AsyncMock(return_value=mock_response)
    mock_post.return_value.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(aiohttp.ClientSession, "post", mock_post)
    
    result = await adapter.send({"test": True}, adapter.config)
    assert result["success"] is True
    assert result["response_code"] == 201
    
    await adapter.close()


@pytest.mark.asyncio
async def test_pagerduty_adapter_test_connection(monkeypatch):
    adapter = PagerDutyAdapter({"routing_key": "123456789012345678901234567890"})
    
    mock_response = MagicMock()
    mock_response.status = 202
    mock_response.text = AsyncMock(return_value="accepted")
    
    mock_post = MagicMock()
    mock_post.return_value.__aenter__ = AsyncMock(return_value=mock_response)
    mock_post.return_value.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(aiohttp.ClientSession, "post", mock_post)
    
    success, error = await adapter.test_connection(adapter.config)
    assert success is True
    assert error == ""
    
    await adapter.close()


def test_test_endpoint_failure_propagation(monkeypatch):
    import os
    
    # Patches os.environ and os.getenv to bypass authentication checking in middlewares
    orig_get = os.environ.get
    monkeypatch.setattr(os.environ, "get", lambda key, default=None: "false" if key == "AUTH_ENABLED" else orig_get(key, default))
    
    orig_getenv = os.getenv
    monkeypatch.setattr(os, "getenv", lambda key, default=None: "false" if key == "AUTH_ENABLED" else orig_getenv(key, default))

    os.environ['AUTH_ENABLED'] = 'false'
    os.environ['SEMABRIDGE_DATABASE_URL'] = 'sqlite:///file::memory:?cache=shared&uri=true'
    os.environ['SEMABRIDGE_DB_BACKEND'] = 'orm'

    from semabridge.repository.orm.session_factory import db_manager, reset_engine
    from semabridge.repository.orm.base import Base
    reset_engine()
    db_manager.reset()
    engine = db_manager.get_engine()
    Base.metadata.create_all(bind=engine)

    from semabridge.notifications.models import NotificationChannel
    from semabridge.notifications.constants import NotificationChannelType
    from uuid import uuid4
    import json
    from semabridge.notifications.utils.crypto import NotificationCrypto
    crypto = NotificationCrypto()

    # Create a test channel in DB
    channel_id = uuid4()
    
    # We encrypt a valid-looking webhook URL config
    config_data = {"webhook_url": "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX"}
    encrypted_config = crypto.encrypt(json.dumps(config_data))
    
    channel = NotificationChannel(
        id=channel_id,
        name="Test Slack",
        channel_type=NotificationChannelType.SLACK,
        config_json=encrypted_config,
        enabled=True,
    )
    with db_manager.get_session() as session:
        session.add(channel)
        session.commit()

    # Mock the SlackAdapter's send to return failure
    mock_send = AsyncMock(return_value={
        "success": False,
        "error": "Session is closed",
        "response_code": None,
    })
    monkeypatch.setattr(SlackAdapter, "send", mock_send)

    from semabridge.api.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        response = client.post(f"/api/settings/notification-channels/{channel_id}/test")

    # Assert that it returns HTTP 500
    assert response.status_code == 500
    assert "Slack delivery failed: Session is closed" in response.json()["detail"]


def test_list_notification_logs_parameters(monkeypatch):
    import os
    
    # Patches os.environ and os.getenv to bypass authentication checking in middlewares
    orig_get = os.environ.get
    monkeypatch.setattr(os.environ, "get", lambda key, default=None: "false" if key == "AUTH_ENABLED" else orig_get(key, default))
    
    orig_getenv = os.getenv
    monkeypatch.setattr(os, "getenv", lambda key, default=None: "false" if key == "AUTH_ENABLED" else orig_getenv(key, default))

    os.environ['AUTH_ENABLED'] = 'false'
    os.environ['SEMABRIDGE_DATABASE_URL'] = 'sqlite:///file::memory:?cache=shared&uri=true'
    os.environ['SEMABRIDGE_DB_BACKEND'] = 'orm'

    from semabridge.repository.orm.session_factory import db_manager, reset_engine
    from semabridge.repository.orm.base import Base
    reset_engine()
    db_manager.reset()
    engine = db_manager.get_engine()
    Base.metadata.create_all(bind=engine)

    from semabridge.api.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        # Check standard empty query
        response = client.get("/api/settings/notification-log")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert len(data["items"]) == 0

        # Check since and until parameters YYYY-MM-DD
        response = client.get("/api/settings/notification-log?since=2026-05-26&until=2026-05-27")
        assert response.status_code == 200
        assert response.json()["total"] == 0

        # Check invalid UUID for event_id returns HTTP 400
        response = client.get("/api/settings/notification-log?event_id=not-a-uuid")
        assert response.status_code == 400
        assert "Invalid event_id UUID" in response.json()["detail"]


def test_preview_notification_template(monkeypatch):
    import os
    
    # Patches os.environ and os.getenv to bypass authentication checking in middlewares
    orig_get = os.environ.get
    monkeypatch.setattr(os.environ, "get", lambda key, default=None: "false" if key == "AUTH_ENABLED" else orig_get(key, default))
    
    orig_getenv = os.getenv
    monkeypatch.setattr(os, "getenv", lambda key, default=None: "false" if key == "AUTH_ENABLED" else orig_getenv(key, default))

    os.environ['AUTH_ENABLED'] = 'false'
    os.environ['SEMABRIDGE_DATABASE_URL'] = 'sqlite:///file::memory:?cache=shared&uri=true'
    os.environ['SEMABRIDGE_DB_BACKEND'] = 'orm'

    from semabridge.api.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        # Happy path: preview template with variables using custom sample_context
        payload = {
            "title_template": "🚨 {{ level_str }} — {{ title }}",
            "body_template": "Project: {{ project_id }}. Code: {{ payload.warehouse }}.",
            "sample_context": {
                "title": "Custom Test Failure",
                "level_str": "CRITICAL",
                "project_id": "fabric-prod-test",
                "payload": {
                    "warehouse": "fabric-west-1"
                }
            }
        }
        response = client.post("/api/settings/notification-templates/preview", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "🚨 CRITICAL — Custom Test Failure"
        assert data["body"] == "Project: fabric-prod-test. Code: fabric-west-1."

        # Backend safe defaults: preview templates with empty templates or missing/partial fields
        payload_defaults = {
            "title_template": "Default {{ title }}",
            "body_template": "Default Body {{ level_str }}"
        }
        response = client.post("/api/settings/notification-templates/preview", json=payload_defaults)
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Default Fabric Sync Failed"
        assert data["body"] == "Default Body ERROR"

        # Safe defaults when fields are entirely empty or missing
        payload_empty = {}
        response = client.post("/api/settings/notification-templates/preview", json=payload_empty)
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == ""
        assert data["body"] == ""

        # Improved validation error: sending invalid schema types (e.g. sample_context is a string)
        payload_invalid = {
            "sample_context": "this is a string, not a dict"
        }
        response = client.post("/api/settings/notification-templates/preview", json=payload_invalid)
        assert response.status_code == 422
        data = response.json()
        assert "Validation failed" in data["detail"]["message"]
        assert "sample_context" in data["detail"]["invalid_schema_paths"]
        assert len(data["detail"]["errors"]) > 0

        # Template syntax error (Jinja parsing failure) returns HTTP 400 with details
        payload_syntax_error = {
            "title_template": "🚨 {{ unclosed_jinja_placeholder",
            "body_template": "Correct body"
        }
        response = client.post("/api/settings/notification-templates/preview", json=payload_syntax_error)
        assert response.status_code == 400
        data = response.json()
        assert "Template rendering failed" in data["detail"]


def test_sync_orchestrator_notification_emission(monkeypatch):
    import os
    from unittest.mock import MagicMock
    
    # Bypass auth
    orig_get = os.environ.get
    monkeypatch.setattr(os.environ, "get", lambda key, default=None: "false" if key == "AUTH_ENABLED" else orig_get(key, default))
    
    os.environ['AUTH_ENABLED'] = 'false'
    os.environ['SEMABRIDGE_DATABASE_URL'] = 'sqlite:///file::memory:?cache=shared&uri=true'
    os.environ['SEMABRIDGE_DB_BACKEND'] = 'orm'

    from semabridge.repository.orm.session_factory import db_manager, reset_engine
    from semabridge.repository.orm.base import Base
    reset_engine()
    db_manager.reset()
    engine = db_manager.get_engine()
    Base.metadata.create_all(bind=engine)

    from semabridge.sync.orchestrator import SyncOrchestrator
    from semabridge.sync.repository import SyncRepository
    from semabridge.sync.models import SyncConfig, SyncDirection

    repo = SyncRepository()
    orchestrator = SyncOrchestrator(repo)
    
    # Mock NotificationService
    mock_notification_svc = MagicMock()
    mock_emit_sync = MagicMock()
    mock_notification_svc.emit_sync = mock_emit_sync
    orchestrator._notification_service = mock_notification_svc

    # Mock item discovery so it doesn't fail on local file systems
    orchestrator._discover_items = MagicMock(return_value=[])

    config = SyncConfig(
        direction=SyncDirection.PBIX_TO_SNOWFLAKE,
        pbix_folder="c:\\temp",
        snowflake_schema="PUBLIC"
    )

    # Trigger a successful sync
    job = orchestrator.run(config)
    assert job.status.value == "completed"

    # Confirm emission of two events: Sync Started and Sync Completed Successfully
    assert mock_emit_sync.call_count == 2
    
    # Verify Sync Started
    started_call = mock_emit_sync.call_args_list[0][0][0]
    assert started_call.title == "Sync Started"
    assert started_call.level == 16  # INFO
    assert started_call.project_id == "c:\\temp"
    assert started_call.sync_job_id == job.job_id
    assert started_call.payload["mode"] == "pbix_to_snowflake"

    # Verify Sync Completed Successfully
    completed_call = mock_emit_sync.call_args_list[1][0][0]
    assert completed_call.title == "Sync Completed Successfully"
    assert completed_call.level == 16  # INFO
    assert completed_call.project_id == "c:\\temp"
    assert completed_call.sync_job_id == job.job_id
    assert completed_call.payload["datasets"] == 0
    assert completed_call.payload["mode"] == "pbix_to_snowflake"

    # Reset mock and trigger a failure sync
    mock_emit_sync.reset_mock()
    
    # Return 1 real SyncJobItem so it executes sequential processing without serialization issues
    from semabridge.sync.models import SyncJobItem, SyncItemStatus
    orchestrator._discover_items = lambda config, job_id: [
        SyncJobItem(
            job_id=job_id,
            model_name="dummy_model",
            source_path="c:\\temp\\dummy.pbix",
            status=SyncItemStatus.QUEUED
        )
    ]
    
    job_failed = orchestrator.run(config)
    assert job_failed.status.value == "failed"

    # Confirm emission of two events: Sync Started and Sync Failed
    assert mock_emit_sync.call_count == 2
    
    failed_call = mock_emit_sync.call_args_list[1][0][0]
    assert failed_call.title == "Sync Failed"
    assert failed_call.level == 4  # ERROR
    assert failed_call.project_id == "c:\\temp"
    assert failed_call.sync_job_id == job_failed.job_id
    assert failed_call.message == "All 1 items failed"
    assert failed_call.payload["failed_stage"] == "execution"


def test_execution_engine_notification_emission(monkeypatch):
    import os
    from unittest.mock import MagicMock
    from semabridge.core.execution_engine import ExecutionEngine, RunContext
    from semabridge.core.behavior import ConnectorBehavior
    from semabridge.core.run_summary import RunStatus
    from types import SimpleNamespace
    
    # Bypass auth
    orig_get = os.environ.get
    monkeypatch.setattr(os.environ, "get", lambda key, default=None: "false" if key == "AUTH_ENABLED" else orig_get(key, default))
    
    os.environ['AUTH_ENABLED'] = 'false'
    
    # Mock NotificationService
    mock_emit_sync = MagicMock()
    
    class MockNotificationService:
        def __init__(self, *args, **kwargs):
            pass
        def emit_sync(self, event):
            mock_emit_sync(event)
            return True
            
    monkeypatch.setattr("semabridge.notifications.services.notification_service.NotificationService", MockNotificationService)
    
    engine = ExecutionEngine()
    context = RunContext(
        project_id="TestProject",
        run_id="run-test-engine-123",
        config=SimpleNamespace(targets=[], target=None),
        start_time=100.0,
        source_type="fabric",
        target_type="snowflake",
        behavior=ConnectorBehavior(),
    )
    context.sml_model = SimpleNamespace(tables=[])
    context.source_artifact_id = "src-art"
    context.sml_snapshot_id = "sml-snap"
    context.target_artifact_path = "tgt-path"
    context.routing_summary = {}
    
    # Mock self.db_manager inside finalize methods
    engine.db_manager = MagicMock()
    
    # Trigger finalization on SUCCESS (should skip notifications)
    summary = engine._step10_finalize(context, RunStatus.SUCCESS)
    assert mock_emit_sync.call_count == 0
    
    # Trigger finalization on FAILED (should emit failure notification)
    summary_fail = engine._step10_finalize(context, RunStatus.FAILED)
    assert mock_emit_sync.call_count == 1
    
    fail_call = mock_emit_sync.call_args_list[0][0][0]
    assert fail_call.title == "Sync Failed"
    assert fail_call.level == 4
    assert fail_call.project_id == "TestProject"
    assert fail_call.sync_job_id == "run-test-engine-123"
