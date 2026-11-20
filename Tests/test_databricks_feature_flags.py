"""Tests for Databricks environment flags registered in core/feature_flags.py."""

from __future__ import annotations

from semabridge.core import feature_flags as ff


def test_databricks_flags_are_registered_with_real_defaults(monkeypatch):
    expected_defaults = {
        "DATABRICKS_HOST": None,
        "DATABRICKS_AUTH_TYPE": "pat",
        "DATABRICKS_TOKEN": None,
        "DATABRICKS_CLIENT_ID": None,
        "DATABRICKS_CLIENT_SECRET": None,
        "DATABRICKS_WAREHOUSE_ID": None,
        "DATABRICKS_CATALOG": "main",
        "DATABRICKS_SCHEMA": "semabridge",
    }
    for name, default in expected_defaults.items():
        flag = ff.get_flag(name)
        assert flag is not None, f"Missing feature flag {name}"
        assert flag.default == default

    for flag in ff.FLAGS:
        monkeypatch.delenv(flag.name, raising=False)
    assert ff.non_default_flags() == []


def test_databricks_secret_flags_are_masked_in_report_and_logs(monkeypatch):
    for flag in ff.FLAGS:
        monkeypatch.delenv(flag.name, raising=False)

    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-super-secret-token-value")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "sp-super-secret-client-value")
    monkeypatch.setenv("DATABRICKS_CATALOG", "analytics")

    report = {entry["name"]: entry for entry in ff.flag_report()}
    assert report["DATABRICKS_TOKEN"]["effective"] == "***"
    assert report["DATABRICKS_TOKEN"]["default"] is None
    assert report["DATABRICKS_TOKEN"]["is_set"] is True
    assert report["DATABRICKS_CLIENT_SECRET"]["effective"] == "***"
    assert report["DATABRICKS_CLIENT_SECRET"]["default"] is None
    assert report["DATABRICKS_CATALOG"]["effective"] == "analytics"
    assert report["DATABRICKS_CATALOG"]["is_default"] is False

    formatted = ff.format_non_default_flags()
    assert "dapi-super-secret-token-value" not in formatted
    assert "sp-super-secret-client-value" not in formatted
    assert "DATABRICKS_TOKEN=***" in formatted
    assert "DATABRICKS_CLIENT_SECRET=***" in formatted
    assert "DATABRICKS_CATALOG=analytics (default: main)" in formatted
