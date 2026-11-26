"""Unit and integration tests for Webhook Security, Drift Schedules, and Force-Refresh.

Verifies:
1. Webhook URL security validation (HTTPS only, no embedded credentials, SSRF prevention against private/loopback IPs).
2. Schedule creation webhook validation.
3. Schedule restore on startup re-registers background runner jobs.
4. Schedule list enrichment with next_run time.
5. Force-refresh flag invalidates cache and triggers fresh warehouse fetch.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from semabridge.governance.drift import (
    save_deploy_baseline,
    validate_webhook_url,
)
from semabridge.governance.drift.introspection_cache import get_cached_definition, set_cached_definition
from semabridge.governance.drift.models import ViewDefinition
from semabridge.governance.drift_service import (
    _get_scheduler,
    _load_state,
    _save_state,
    clear_drift_events,
    create_schedule,
    delete_schedule,
    list_schedules,
    restore_drift_schedules,
    run_drift_check,
)


@pytest.fixture(autouse=True)
def clean_drift_environment():
    """Isolate drift events and schedules for test."""
    clear_drift_events(_allow_all_for_testing=True)
    state = _load_state()
    orig_scheds = list(state.get("schedules") or [])
    state["schedules"] = []
    _save_state(state)
    yield
    clear_drift_events(_allow_all_for_testing=True)
    state = _load_state()
    state["schedules"] = orig_scheds
    _save_state(state)


def test_webhook_validation_valid_https():
    """Valid HTTPS URL targeting public hostname passes validation without error."""
    valid_url = "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX"
    # Should not raise any exception
    validate_webhook_url(valid_url)


def test_webhook_validation_rejects_non_https():
    """Non-HTTPS schemes (http, ftp, file) are strictly rejected."""
    with pytest.raises(ValueError, match="must use the HTTPS protocol"):
        validate_webhook_url("http://api.example.com/webhook")

    with pytest.raises(ValueError, match="must use the HTTPS protocol"):
        validate_webhook_url("ftp://api.example.com/webhook")


def test_webhook_validation_rejects_embedded_credentials():
    """Embedded user credentials in webhook URL are rejected."""
    with pytest.raises(ValueError, match="must not contain embedded user credentials"):
        validate_webhook_url("https://admin:secret@api.example.com/alerts")


def test_webhook_validation_rejects_localhost_and_internal_hostnames():
    """Localhost and internal domain suffixes (.local, .internal) are rejected."""
    with pytest.raises(ValueError, match="targets an internal or loopback host"):
        validate_webhook_url("https://localhost/webhook")

    with pytest.raises(ValueError, match="targets an internal or loopback host"):
        validate_webhook_url("https://app.localhost/webhook")

    with pytest.raises(ValueError, match="targets an internal or loopback host"):
        validate_webhook_url("https://intranet.local/alerts")

    with pytest.raises(ValueError, match="targets an internal or loopback host"):
        validate_webhook_url("https://service.internal/alerts")


def test_webhook_validation_rejects_private_and_loopback_ips():
    """Private IPv4/IPv6 and loopback ranges (127.0.0.1, 10.x, 192.168.x) are rejected."""
    with pytest.raises(ValueError, match="targets a private or restricted IP address"):
        validate_webhook_url("https://127.0.0.1:8080/hook")

    with pytest.raises(ValueError, match="targets a private or restricted IP address"):
        validate_webhook_url("https://10.0.0.1/hook")

    with pytest.raises(ValueError, match="targets a private or restricted IP address"):
        validate_webhook_url("https://192.168.1.100/hook")

    with pytest.raises(ValueError, match="targets a private or restricted IP address"):
        validate_webhook_url("https://[::1]/hook")


def test_create_schedule_validates_webhook_url():
    """Creating a schedule with an invalid webhook URL raises ValueError."""
    project_id = "proj-sched-test"

    # Insecure URL raises ValueError
    with pytest.raises(ValueError, match="must use the HTTPS protocol"):
        create_schedule(
            project_id=project_id,
            interval_minutes=60,
            webhook_url="http://insecure-alerts.com",
        )

    # Valid HTTPS URL succeeds
    sched = create_schedule(
        project_id=project_id,
        interval_minutes=30,
        webhook_url="https://alerts.publicservice.com/webhook",
    )
    assert sched["id"].startswith("sched-")
    assert sched["interval_minutes"] == 30
    assert sched["webhook_url"] == "https://alerts.publicservice.com/webhook"

    # Cleanup
    delete_schedule(sched["id"])


def test_restore_drift_schedules_registers_background_jobs():
    """restore_drift_schedules re-registers all saved enabled schedules into APScheduler."""
    state = _load_state()
    sched1 = {
        "id": "sched-test-alpha",
        "project_id": "proj-restore-alpha",
        "interval_minutes": 15,
        "enabled": True,
        "created_at": "2026-11-23T12:00:00Z",
    }
    sched2 = {
        "id": "sched-test-beta",
        "project_id": "proj-restore-beta",
        "interval_minutes": 45,
        "enabled": False,  # Disabled, should NOT be registered
        "created_at": "2026-11-23T12:00:00Z",
    }
    state["schedules"] = [sched1, sched2]
    _save_state(state)

    restored = restore_drift_schedules()
    assert restored >= 1

    scheduler = _get_scheduler()
    job = scheduler.get_job("sched-test-alpha")
    assert job is not None

    # Disabled schedule was not registered
    disabled_job = scheduler.get_job("sched-test-beta")
    assert disabled_job is None

    # list_schedules reports next_run
    listed = list_schedules(project_id="proj-restore-alpha")
    assert len(listed) == 1
    assert listed[0].get("next_run") is not None

    # Cleanup
    delete_schedule("sched-test-alpha")


def test_force_refresh_invalidates_cache_and_queries_warehouse():
    """force_refresh=True invalidates hot TTL cache and forces a fresh query to the warehouse."""
    project_id = "proj-force-refresh"
    view_name = "DB.SCH.CACHE_VIEW"

    save_deploy_baseline(
        project_id=project_id,
        platform="SNOWFLAKE",
        view_name=view_name,
        definition_text="",
        metrics={"KPI": "SUM(amount)"},
        deployed_at="2026-11-23T10:00:00Z",
    )

    # Pre-seed cache with definition text
    set_cached_definition("SNOWFLAKE", "test-ident", view_name, "SELECT 1 AS VAL")
    assert get_cached_definition("SNOWFLAKE", "test-ident", view_name) is not None

    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = None
    mock_cur.description = [("object_kind",), ("object_name",), ("property",), ("property_value",)]
    mock_cur.fetchall.return_value = [("METRIC", "KPI", "EXPRESSION", "SUM(amount)")]
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    mock_dom = MagicMock()
    mock_dom.domain = "Cache Domain"
    mock_dom.semantic_view_name = view_name
    mock_dom.metrics = [MagicMock(name="KPI", dax_expression="SUM(amount)", sql_expression="SUM(amount)")]

    with patch("semabridge.ai.project_connection.resolve_project_warehouse_config", return_value={"type": "snowflake"}), \
         patch("semabridge.connectors.snowflake_connection.get_snowflake_connect_kwargs", return_value={"account": "test", "user": "test"}), \
         patch("snowflake.connector.connect", return_value=mock_conn), \
         patch("semabridge.connectors.sql_guard.guard_connection", return_value=mock_conn), \
         patch("semabridge.ontology.store.ontology_store.domains_for", return_value=[mock_dom]):

        # Run with force_refresh=True
        res = run_drift_check(project_id=project_id, view=view_name, force_refresh=True)
        assert res["status"] == "IN_SYNC"
        assert res["category_counts"]["EXPRESSION_DRIFT"] == 0

        # Warehouse mock was queried (cursor called) because cache was forcibly invalidated
        assert mock_cur.fetchall.called
