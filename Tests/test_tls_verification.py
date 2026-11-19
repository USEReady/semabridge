"""Outbound token requests verify TLS by default.

Behind a TLS-intercepting proxy, SEMABRIDGE_TLS_CA_BUNDLE points at the
proxy CA; SEMABRIDGE_INSECURE_SKIP_TLS_VERIFY is an explicit opt-out.
"""
from semabridge.utils.tls import requests_verify


def test_verifies_by_default(monkeypatch):
    monkeypatch.delenv("SEMABRIDGE_TLS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("SEMABRIDGE_INSECURE_SKIP_TLS_VERIFY", raising=False)
    assert requests_verify() is True


def test_uses_the_proxy_ca_bundle(monkeypatch, tmp_path):
    ca = tmp_path / "proxy-ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\n")
    monkeypatch.setenv("SEMABRIDGE_TLS_CA_BUNDLE", str(ca))
    assert requests_verify() == str(ca)


def test_missing_bundle_falls_back_to_verifying(monkeypatch, tmp_path):
    monkeypatch.setenv("SEMABRIDGE_TLS_CA_BUNDLE", str(tmp_path / "nope.pem"))
    monkeypatch.delenv("SEMABRIDGE_INSECURE_SKIP_TLS_VERIFY", raising=False)
    assert requests_verify() is True


def test_explicit_opt_out_disables_checks(monkeypatch):
    monkeypatch.delenv("SEMABRIDGE_TLS_CA_BUNDLE", raising=False)
    monkeypatch.setenv("SEMABRIDGE_INSECURE_SKIP_TLS_VERIFY", "true")
    assert requests_verify() is False
