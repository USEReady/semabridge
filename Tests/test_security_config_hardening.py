"""Regression tests: path allow-list, admin-only local folders, config parse
errors, settings aliases, feature-flag registry, failed-result drop ledger."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import semabridge.auth.file_access as fa
from semabridge.domain.exceptions import PermissionError as DomainPermissionError


@pytest.fixture
def root(tmp_path, monkeypatch):
    allowed = (tmp_path / "allowed").resolve()
    allowed.mkdir()
    monkeypatch.setattr(fa, "allowed_source_roots", lambda: [allowed])
    return allowed


def _request(user_id="7"):
    return SimpleNamespace(state=SimpleNamespace(user_id=user_id))


# 1. browse-directory -------------------------------------------------------

def test_browse_lists_only_allowed_roots_without_base_path(root):
    from semabridge.api.browse import browse_directory

    listing = browse_directory(None)
    assert [item["path"] for item in listing] == [str(root).replace("\\", "/")]


def test_browse_rejects_filesystem_root_and_outside_dirs(root, tmp_path):
    from semabridge.api.browse import browse_directory

    for outside in ("/", str(tmp_path), "/etc"):
        with pytest.raises(HTTPException) as exc:
            browse_directory(outside)
        assert exc.value.status_code == 403


def test_browse_lists_subdirs_but_not_symlink_escapes(root, tmp_path):
    from semabridge.api.browse import browse_directory

    (root / "models").mkdir()
    (tmp_path / "secret").mkdir()
    try:
        (root / "escape").symlink_to(tmp_path / "secret", target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation not permitted in this environment")
    names = [item["name"] for item in browse_directory(str(root))]
    assert names == ["models"]


def test_browse_has_no_second_allow_list():
    import semabridge.api.browse as browse

    assert not hasattr(browse, "_is_within_allowed_roots")


# 2. local folders: admin only, no system paths ------------------------------

def test_local_folder_registration_requires_admin(monkeypatch, tmp_path):
    from semabridge.api import settings_api
    from semabridge.api.services import project_ownership_service as pos

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr(pos, "_is_admin_user", lambda _uid: False)
    body = settings_api.LocalFolderCreate(tag_name="t", absolute_path=str(tmp_path))
    with pytest.raises(HTTPException) as exc:
        settings_api.save_local_folder(body, _request(), db=None)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("path", ["/", "/etc", "/usr", "/usr/local", "~"])
def test_local_folder_rejects_system_paths(monkeypatch, path):
    from semabridge.api import settings_api

    monkeypatch.setenv("AUTH_ENABLED", "false")  # dev user is admin
    body = settings_api.LocalFolderCreate(tag_name="t", absolute_path=path)
    with pytest.raises(HTTPException) as exc:
        settings_api.save_local_folder(body, _request(), db=None)
    assert exc.value.status_code == 400


def test_local_folder_symlink_to_system_dir_rejected(monkeypatch, tmp_path):
    from semabridge.api import settings_api

    monkeypatch.setenv("AUTH_ENABLED", "false")
    link = tmp_path / "innocent"
    try:
        link.symlink_to("/etc", target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation not permitted in this environment")
    with pytest.raises(HTTPException) as exc:
        settings_api._resolve_existing_directory(str(link))
    assert exc.value.status_code == 400


def test_local_folder_specific_project_dir_accepted(tmp_path):
    from semabridge.api import settings_api

    project_dir = tmp_path / "pbix"
    project_dir.mkdir()
    assert settings_api._resolve_existing_directory(str(project_dir)) == project_dir.resolve()


# 3. paths sent to sync / project endpoints ----------------------------------

def test_sync_job_rejects_paths_outside_allowed_roots(root):
    from semabridge.api import sync_router

    for field in ("pbix_folder", "source_path", "file_path"):
        req = sync_router.StartSyncRequest(direction="pbix_to_snowflake", **{field: "/etc"})
        with pytest.raises(HTTPException) as exc:
            sync_router.start_sync_job(req)
        assert exc.value.status_code == 403, field


def test_sync_job_path_inside_root_is_resolved(root):
    from semabridge.api import sync_router

    (root / "m").mkdir()
    assert sync_router._validated_path(str(root / "x" / ".." / "m"), expect="dir", field="f") == str(root / "m")


def test_project_payload_paths_validated(root):
    from semabridge.api.services.project_projects_impl import _validate_payload_source_paths

    _validate_payload_source_paths({"source": {"pbix_path": str(root / "a.pbix")}})
    with pytest.raises(DomainPermissionError):
        _validate_payload_source_paths({"source": {"pbix_folder": "/etc"}})
    with pytest.raises(DomainPermissionError):
        _validate_payload_source_paths({"pbix_file_path": "/etc/passwd"})


def test_generate_config_rejects_outside_pbix_folder(root):
    from semabridge.api.services.core_config_impl import generate_config

    with pytest.raises(DomainPermissionError):
        asyncio.run(generate_config({"sourceType": "pbix", "pbixFolder": "/etc"}))


# 4. /api/repo/file containment ---------------------------------------------

def test_repo_file_rejects_sibling_prefix_dir(tmp_path, monkeypatch):
    from semabridge.api import repo_router

    models = tmp_path / "models"
    models.mkdir()
    sibling = tmp_path / "models-secrets"
    sibling.mkdir()
    (sibling / "key.txt").write_text("secret")
    (models / "ok.txt").write_text("fine")
    monkeypatch.setattr(repo_router, "_resolve_models_path", lambda: models)

    assert repo_router.get_file_content("ok.txt")["content"] == "fine"
    with pytest.raises(HTTPException) as exc:
        repo_router.get_file_content("../models-secrets/key.txt")
    assert exc.value.status_code == 403


# 5. corrupt project config is an error, not an empty config ---------------

def test_render_corrupt_stored_config_raises():
    from semabridge.api.services.project_config_store import ConfigPersistenceError, render_config_text

    with pytest.raises(ConfigPersistenceError, match="Project config for p1 is not valid YAML"):
        render_config_text("source: [unclosed", [], "p1")


def test_save_corrupt_config_raises_validation_error():
    from semabridge.api.services.project_config_store import InvalidProjectConfigError, save_project_config

    with pytest.raises(InvalidProjectConfigError, match="not valid YAML"):
        save_project_config("proj-corrupt", "source: [unclosed", reason="test")


def test_mapping_service_parse_strict_and_logged(caplog):
    from semabridge.api.services.mapping_service import _compat_parse_project_cfg_dict
    from semabridge.api.services.project_config_store import ConfigPersistenceError

    with caplog.at_level("WARNING"):
        assert _compat_parse_project_cfg_dict("a: [", project_id="p2") == {}
    assert "not valid YAML" in caplog.text
    with pytest.raises(ConfigPersistenceError):
        _compat_parse_project_cfg_dict("a: [", project_id="p2", strict=True)


def test_modular_project_load_raises_on_corrupt_config(monkeypatch):
    import semabridge.api.services.project_config_store as store
    import semabridge.api.services.project_shared as ps

    monkeypatch.setattr(store, "get_project_config_text", lambda _pid, **_k: "a: [")
    with pytest.raises(store.ConfigPersistenceError):
        ps._compat_load_modular_project("p3")


# 6. explicit schema_name is honoured --------------------------------------

def test_databricks_explicit_schema_name_honoured(monkeypatch):
    from semabridge.core.settings import ConcurrencyConfig, DatabricksConfig

    monkeypatch.setenv("DATABRICKS_SCHEMA", "from_env")
    assert DatabricksConfig(host="h", warehouse_id="w", schema_name="explicit").schema_name == "explicit"
    monkeypatch.delenv("MAX_WORKERS", raising=False)
    assert ConcurrencyConfig(max_workers=7).max_workers == 7


# 7. feature flags ----------------------------------------------------------

def test_feature_flags_registry_and_non_default(monkeypatch):
    from semabridge.core import feature_flags as ff

    required = {
        "SEMABRIDGE_DEMO_MODE", "SEMABRIDGE_DISABLE_DEMO_MODE", "SEMABRIDGE_MOCK_DRIFT",
        "SEMABRIDGE_MOCK_PARITY", "SEMABRIDGE_STAGED_DEPLOY", "SEMABRIDGE_DATE_ALIAS",
        "SEMABRIDGE_DATE_RESOLUTION", "SEMABRIDGE_INFER_RELATIONSHIPS", "SEMABRIDGE_DDL_SYNONYMS",
        "SEMABRIDGE_DDL_COMMENTS", "SEMABRIDGE_SYNC_EXECUTOR", "SEMABRIDGE_ALLOW_LOCAL_DB",
        "SEMABRIDGE_COMPAT_STORE_PATH", "SEMABRIDGE_DEV_AUTO_LOGIN", "AUTH_ENABLED",
        "SEMABRIDGE_MAX_CONCURRENT_RUNS", "SEMABRIDGE_MAX_CONCURRENT_MODELS",
        "SEMABRIDGE_RUN_LEASE_SECONDS", "SEMABRIDGE_READINESS_TIMEOUT_SECONDS",
    }
    assert required <= {f.name for f in ff.FLAGS}
    for flag in ff.FLAGS:
        monkeypatch.delenv(flag.name, raising=False)
    assert ff.non_default_flags() == []
    monkeypatch.setenv("SEMABRIDGE_STAGED_DEPLOY", "1")  # same as default true
    monkeypatch.setenv("SEMABRIDGE_MOCK_DRIFT", "true")
    monkeypatch.setenv("SEMABRIDGE_MAX_CONCURRENT_RUNS", "6")
    assert {f.name for f in ff.non_default_flags()} == {"SEMABRIDGE_MOCK_DRIFT", "SEMABRIDGE_MAX_CONCURRENT_RUNS"}
    text = ff.format_non_default_flags()
    assert "SEMABRIDGE_MAX_CONCURRENT_RUNS=6 (default: 2)" in text


def test_admin_flags_endpoint_requires_admin(monkeypatch):
    from semabridge.api.routers import maintenance_router
    from semabridge.api.services import project_ownership_service as pos

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr(pos, "_is_admin_user", lambda _uid: False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(maintenance_router.feature_flags(_request()))
    assert exc.value.status_code == 403

    monkeypatch.setattr(pos, "_is_admin_user", lambda _uid: True)
    report = asyncio.run(maintenance_router.feature_flags(_request()))
    assert any(f["name"] == "AUTH_ENABLED" for f in report["flags"])


# 8. failed result keeps the drop ledger -------------------------------------

def test_failed_result_keeps_drop_ledger_even_with_bad_record():
    from semabridge.api.services.sync_execution_service import _collect_drop_ledger

    class _Bad:
        entity = "Sales.Broken"
        reason = "unsupported"

        def model_dump(self, mode="json"):
            raise TypeError("not serialisable")

    good = {"entity": "Sales.Amount", "reason": "type"}
    engine = SimpleNamespace(_context=SimpleNamespace(drop_ledger=SimpleNamespace(records=[good, _Bad()])))
    drops = _collect_drop_ledger(engine, "m")
    assert drops[0] == good
    assert drops[1]["entity"] == "Sales.Broken" and drops[1]["ledger_error"] is True
