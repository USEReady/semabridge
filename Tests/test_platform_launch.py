"""Databricks Apps launcher binds to the platform-assigned port."""
from semabridge.platform.launch import port


def test_port_precedence(monkeypatch):
    monkeypatch.delenv("DATABRICKS_APP_PORT", raising=False)
    monkeypatch.delenv("API_PORT", raising=False)
    assert port() == 8000
    monkeypatch.setenv("API_PORT", "8002")
    assert port() == 8002
    monkeypatch.setenv("DATABRICKS_APP_PORT", "8080")
    assert port() == 8080
    monkeypatch.setenv("DATABRICKS_APP_PORT", "not-a-port")
    assert port() == 8002
