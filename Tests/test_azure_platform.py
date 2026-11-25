"""Azure profile: platform (Entra ID) sign-in, the served web app, and the
rule that no API route becomes public through the SPA exemption."""
import pytest

from semabridge.platform.identity import PlatformIdentity, platform_identity, role_for

AZ_HEADERS = {"X-MS-CLIENT-PRINCIPAL-NAME": "ana@contoso.com", "X-MS-CLIENT-PRINCIPAL-ID": "oid-1"}


@pytest.fixture
def azure(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_DEPLOYMENT_PROFILE", "azure")
    monkeypatch.setenv("SEMABRIDGE_PLATFORM_AUTH", "true")
    monkeypatch.delenv("SEMABRIDGE_PLATFORM_ADMINS", raising=False)


def test_headers_ignored_unless_front_door_declared(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_DEPLOYMENT_PROFILE", "azure")
    monkeypatch.delenv("SEMABRIDGE_PLATFORM_AUTH", raising=False)
    assert platform_identity(AZ_HEADERS) is None


def test_headers_ignored_on_local_profile(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_DEPLOYMENT_PROFILE", "local")
    monkeypatch.setenv("SEMABRIDGE_PLATFORM_AUTH", "true")
    assert platform_identity(AZ_HEADERS) is None


def test_azure_identity(azure):
    ident = platform_identity(AZ_HEADERS)
    assert ident == PlatformIdentity("entra_id", "oid-1", "ana@contoso.com", "ana@contoso.com")


@pytest.mark.parametrize("profile, headers, provider", [
    ("snowflake_native", {"Sf-Context-Current-User": "ANA"}, "snowflake"),
    ("databricks_app", {"X-Forwarded-Email": "ana@contoso.com", "X-Forwarded-User": "123"}, "databricks"),
])
def test_other_platforms(monkeypatch, profile, headers, provider):
    monkeypatch.setenv("SEMABRIDGE_DEPLOYMENT_PROFILE", profile)
    monkeypatch.setenv("SEMABRIDGE_PLATFORM_AUTH", "true")
    assert platform_identity(headers).provider == provider


def test_admin_only_from_explicit_list(monkeypatch, azure):
    ident = platform_identity(AZ_HEADERS)
    assert role_for(ident) == "viewer"
    monkeypatch.setenv("SEMABRIDGE_PLATFORM_DEFAULT_ROLE", "admin")
    assert role_for(ident) == "viewer"
    monkeypatch.setenv("SEMABRIDGE_PLATFORM_ADMINS", "boss@contoso.com, ANA@contoso.com")
    assert role_for(ident) == "admin"


def _client():
    from fastapi.testclient import TestClient

    from semabridge.api.main import app

    return TestClient(app)


def test_platform_login_is_404_without_trusted_identity(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_DEPLOYMENT_PROFILE", "local")
    assert _client().post("/auth/platform-login", headers=AZ_HEADERS).status_code == 404


def test_platform_login_provisions_user_and_issues_token(azure, monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-signing-key-0123456789abcdef")
    resp = _client().post("/auth/platform-login", headers=AZ_HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["user"]["email"] == "ana@contoso.com"
    assert body["user"]["role"] == "viewer"
    again = _client().post("/auth/platform-login", headers=AZ_HEADERS).json()
    assert again["user"]["id"] == body["user"]["id"]


def test_no_api_get_route_is_public_through_the_spa_rule():
    from semabridge.api.bootstrap.spa import is_spa_path
    from semabridge.api.main import app

    exposed = sorted(
        r.path for r in app.routes
        if ({"GET", "HEAD"} & (getattr(r, "methods", None) or set()))
        and is_spa_path(r.path) and r.path != "/{full_path:path}"
    )
    assert exposed == [], f"Move these under /api (or reserve their prefix in spa.py): {exposed}"


def test_spa_serves_index_for_client_routes_and_never_for_api(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from semabridge.api.bootstrap.spa import mount_spa

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>app</html>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setenv("SEMABRIDGE_FRONTEND_DIST", str(tmp_path))
    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    assert mount_spa(app)
    c = TestClient(app)
    assert c.get("/projects/42").text == "<html>app</html>"
    assert c.get("/assets/app.js").text == "console.log(1)"
    assert c.get("/api/ping").json() == {"ok": True}
    assert c.get("/api/missing").status_code == 404
    assert c.get("/../../etc/passwd").text == "<html>app</html>"
