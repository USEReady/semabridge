"""Hosted deployments refuse to start on a failed schema step; local logs it."""
import asyncio

import pytest

from semabridge.api.bootstrap import startup_state as ss


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    ss.reset()
    monkeypatch.delenv("SEMABRIDGE_STRICT_STARTUP", raising=False)
    monkeypatch.delenv("SEMABRIDGE_ENV", raising=False)
    yield
    ss.reset()


def test_local_profile_starts_degraded(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_DEPLOYMENT_PROFILE", "local")
    ss.record_failure("alembic_upgrade", RuntimeError("boom"))
    ss.raise_if_startup_failed()  # no raise
    assert ss.failures() == [{"step": "alembic_upgrade", "error": "RuntimeError: boom"}]


def test_hosted_profile_refuses_to_start(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_DEPLOYMENT_PROFILE", "azure")
    ss.record_failure("alembic_upgrade", RuntimeError("boom"))
    with pytest.raises(ss.StartupFailedError, match="alembic_upgrade: RuntimeError: boom"):
        ss.raise_if_startup_failed()


def test_explicit_opt_out_and_opt_in(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_DEPLOYMENT_PROFILE", "azure")
    monkeypatch.setenv("SEMABRIDGE_STRICT_STARTUP", "false")
    ss.record_failure("rls_policies", RuntimeError("x"))
    ss.raise_if_startup_failed()
    monkeypatch.setenv("SEMABRIDGE_DEPLOYMENT_PROFILE", "local")
    monkeypatch.setenv("SEMABRIDGE_STRICT_STARTUP", "true")
    with pytest.raises(ss.StartupFailedError):
        ss.raise_if_startup_failed()


def test_readiness_reports_failed_startup_steps():
    from semabridge.api.controllers.health_controller import readiness

    ss.record_failure("alembic_upgrade", RuntimeError("boom"))
    resp = asyncio.run(readiness())
    assert resp.status_code == 503
    assert b"failed: alembic_upgrade" in resp.body
