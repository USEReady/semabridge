"""Deployment profile resolution, and production never leaks reset tokens."""
import pytest

from semabridge.auth import email_service
from semabridge.core.deployment_profile import (
    DeploymentProfile,
    UnknownDeploymentProfileError,
    current_profile,
    is_production,
)


@pytest.fixture
def no_smtp(monkeypatch):
    monkeypatch.setattr(email_service, "_get_smtp_config", lambda: {"host": "", "user": "", "password": "", "port": 587, "from_addr": ""})


@pytest.mark.parametrize(
    "raw, expected",
    [("", DeploymentProfile.LOCAL), ("azure", DeploymentProfile.AZURE), ("SPCS", DeploymentProfile.SNOWFLAKE_NATIVE),
     ("databricks", DeploymentProfile.DATABRICKS_APP), ("development", DeploymentProfile.LOCAL)],
)
def test_profile_aliases(monkeypatch, raw, expected):
    monkeypatch.setenv("SEMABRIDGE_DEPLOYMENT_PROFILE", raw)
    assert current_profile() is expected


def test_unknown_profile_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_DEPLOYMENT_PROFILE", "aws")
    with pytest.raises(UnknownDeploymentProfileError, match="azure"):
        current_profile()


def test_production_flags(monkeypatch):
    monkeypatch.delenv("SEMABRIDGE_ENV", raising=False)
    monkeypatch.setenv("SEMABRIDGE_DEPLOYMENT_PROFILE", "local")
    assert is_production() is False
    monkeypatch.setenv("SEMABRIDGE_ENV", "production")
    assert is_production() is True
    monkeypatch.delenv("SEMABRIDGE_ENV")
    monkeypatch.setenv("SEMABRIDGE_DEPLOYMENT_PROFILE", "azure")
    assert is_production() is True


def test_hosted_profile_never_returns_or_prints_the_reset_link(monkeypatch, capsys, no_smtp):
    monkeypatch.setenv("SEMABRIDGE_DEPLOYMENT_PROFILE", "azure")
    sent, url = email_service.send_password_reset_email("a@example.com", "secret-token-123", "alice")
    assert (sent, url) == (False, None)
    assert "secret-token-123" not in capsys.readouterr().out


def test_local_profile_keeps_the_developer_shortcut(monkeypatch, capsys, no_smtp):
    monkeypatch.setenv("SEMABRIDGE_DEPLOYMENT_PROFILE", "local")
    monkeypatch.delenv("SEMABRIDGE_ENV", raising=False)
    _, url = email_service.send_password_reset_email("a@example.com", "tok-1", "alice")
    assert url and url.endswith("/auth/reset-password/tok-1")
