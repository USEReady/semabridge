"""Multi-PBIX naming and collision-detection tests (Part D).

Covers:
  - get_pbix_deployment_view_name() / find_pbix_view_name_collisions() (pure logic)
  - Both live enforcement points independently:
      1. Project-configuration time (projects_controller.create_project)
      2. Job-construction time (_build_sync_jobs, shared by dry-run AND deploy)
  - The specific "fixed in one place, not the other" failure mode: each site
    is proven to catch a collision even when the OTHER site's check is
    disabled/never wired, so a future regression that removes one wiring
    point (but not the other) would be caught by this suite.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

import pytest

os.environ.setdefault("SEMABRIDGE_DATABASE_URL", "sqlite:///./test_pbix_view_name_collision.sqlite")
os.environ.setdefault("AUTH_ENABLED", "false")

if "psycopg2" not in sys.modules:
    _psycopg2 = types.ModuleType("psycopg2")
    _psycopg2.__version__ = "2.9.9"
    _psycopg2.apilevel = "2.0"
    _psycopg2.threadsafety = 2
    _psycopg2.paramstyle = "pyformat"
    _psycopg2.Error = Exception
    _psycopg2.connect = lambda *args, **kwargs: None
    sys.modules["psycopg2"] = _psycopg2
    sys.modules["psycopg2.extensions"] = types.ModuleType("psycopg2.extensions")
    sys.modules["psycopg2.extras"] = types.ModuleType("psycopg2.extras")

from semabridge.domain.exceptions import ValidationError
from semabridge.utils.name_translator import (
    find_pbix_view_name_collisions,
    get_pbix_deployment_view_name,
    validate_no_pbix_view_name_collisions,
)

COLLIDING_A = "C:/Reports/Sales Report.pbix"
COLLIDING_B = "C:/Reports/Sales_Report.pbix"
DISTINCT_C = "C:/Reports/Marketing Analysis.pbix"


# ---------------------------------------------------------------------------
# 1. Pure naming/collision logic
# ---------------------------------------------------------------------------


def test_get_pbix_deployment_view_name_derives_from_filename_not_project():
    assert get_pbix_deployment_view_name("C:/Reports/Sales Report.pbix", "snowflake") == "SALES_REPORT_SEMANTIC"


def test_get_pbix_deployment_view_name_handles_leading_digit_and_reserved_chars():
    # get_target_deployment_name()'s own regex-strip alone would leave "1_SALES"
    # starting with a digit, invalid as an unquoted Snowflake identifier.
    # sanitize_table_name() (composed first) fixes this.
    name = get_pbix_deployment_view_name("C:/Reports/1_Sales$Report.pbix", "snowflake")
    assert not name[0].isdigit()
    assert name.endswith("_SEMANTIC")


def test_find_collisions_detects_space_vs_underscore_variants():
    collisions = find_pbix_view_name_collisions([COLLIDING_A, COLLIDING_B, DISTINCT_C])
    assert len(collisions) == 1
    (view_name, paths), = collisions.items()
    assert view_name == "SALES_REPORT_SEMANTIC"
    assert set(paths) == {COLLIDING_A, COLLIDING_B}


def test_find_collisions_empty_when_all_distinct():
    collisions = find_pbix_view_name_collisions([COLLIDING_A, DISTINCT_C])
    assert collisions == {}


def test_validate_raises_with_actionable_message_naming_both_files():
    with pytest.raises(ValidationError) as excinfo:
        validate_no_pbix_view_name_collisions([COLLIDING_A, COLLIDING_B])
    message = str(excinfo.value)
    assert "Sales Report.pbix" in message
    assert "Sales_Report.pbix" in message
    assert "SALES_REPORT_SEMANTIC" in message
    assert "Rename" in message


def test_validate_does_not_raise_for_distinct_names():
    validate_no_pbix_view_name_collisions([COLLIDING_A, DISTINCT_C])


# ---------------------------------------------------------------------------
# 2. Call site 1: _build_sync_jobs() — shared by dry-run AND deploy
# ---------------------------------------------------------------------------


def test_build_sync_jobs_rejects_colliding_filenames(tmp_path):
    from semabridge.api.services import sync_execution_service as ses

    file_a = tmp_path / "Sales Report.pbix"
    file_b = tmp_path / "Sales_Report.pbix"
    file_a.write_bytes(b"a")
    file_b.write_bytes(b"b")

    source_cfg = {"type": "pbix", "models": [str(file_a), str(file_b)]}
    with pytest.raises(ValidationError, match="Naming collision detected"):
        ses._build_sync_jobs({"source": source_cfg, "targets": [{"type": "snowflake"}]})


def test_build_sync_jobs_collision_check_covers_dry_run_and_deploy_identically(tmp_path, monkeypatch):
    """execute_sync_request() calls _build_sync_jobs() exactly once regardless
    of payload["dry_run"] — proving the same collision check applies to both
    a dry-run request and a deploy request, because they share this one
    job-construction step. There is no separate code path for this to drift
    out of sync between the two.
    """
    from semabridge.api.services import sync_execution_service as ses

    file_a = tmp_path / "Sales Report.pbix"
    file_b = tmp_path / "Sales_Report.pbix"
    file_a.write_bytes(b"a")
    file_b.write_bytes(b"b")

    source_cfg = {"type": "pbix", "models": [str(file_a), str(file_b)]}
    config = {"source": source_cfg, "targets": [{"type": "snowflake"}]}

    monkeypatch.setattr(ses, "reload_settings", lambda: None)
    monkeypatch.setattr(ses, "_load_config", lambda payload, normalizer: ("semabridge.yaml", config))

    for dry_run_flag in (True, False):
        with pytest.raises(ValidationError, match="Naming collision detected"):
            ses.execute_sync_request({"dry_run": dry_run_flag}, lambda content, *args: content)


def test_build_sync_jobs_catches_collision_even_if_config_time_check_was_never_wired(tmp_path):
    """Simulates 'the CreateProjectRequest-time check was fixed/removed' by
    calling _build_sync_jobs() in complete isolation from
    projects_controller.create_project — proving the execution-time site is a
    genuinely independent line of defense, not a fig leaf that only ever runs
    alongside the config-time check.
    """
    from semabridge.api.services import sync_execution_service as ses

    file_a = tmp_path / "Sales Report.pbix"
    file_b = tmp_path / "Sales_Report.pbix"
    file_a.write_bytes(b"a")
    file_b.write_bytes(b"b")

    source_cfg = {"type": "pbix", "models": [str(file_a), str(file_b)]}
    with pytest.raises(ValidationError, match="Naming collision detected"):
        ses._build_sync_jobs({"source": source_cfg, "targets": [{"type": "snowflake"}]})


# ---------------------------------------------------------------------------
# 3. Call site 2: project-configuration time (create_project)
# ---------------------------------------------------------------------------


def test_create_project_rejects_colliding_filenames_before_touching_compat_store(monkeypatch):
    import semabridge.api.controllers.projects_controller as pc

    called = {"create_project_compat": False}

    async def _fake_create_project_compat(request_dict):
        called["create_project_compat"] = True
        return {"id": "should-not-get-here"}

    monkeypatch.setattr(pc, "create_project_compat", _fake_create_project_compat)
    monkeypatch.setattr(pc, "require_request_user_id", lambda request: None)

    payload = pc.CreateProjectRequest(
        name="multi-pbix-collision-test",
        source={"type": "pbix", "models": [COLLIDING_A, COLLIDING_B]},
    )

    with pytest.raises(ValidationError, match="Naming collision detected"):
        asyncio.run(pc.create_project(object(), payload))

    assert called["create_project_compat"] is False


def test_create_project_catches_collision_even_if_build_sync_jobs_check_was_never_wired(monkeypatch):
    """Simulates 'the _build_sync_jobs()-time check was fixed/removed' by
    monkeypatching sync_execution_service's collision validator to a no-op —
    proving create_project()'s own, separately-imported call to
    validate_no_pbix_view_name_collisions() still independently catches the
    collision. This is the concrete regression test for the 'fixed in one
    place, not the other' failure mode: if this test passed only because the
    OTHER site happened to also be wired, patching that other site to a no-op
    would make this test fail too — it doesn't, because create_project calls
    its own, separate import of the shared validator.
    """
    import semabridge.api.controllers.projects_controller as pc
    from semabridge.api.services import sync_execution_service as ses

    # Simulate the execution-time site being un-wired/broken.
    monkeypatch.setattr(ses, "validate_no_pbix_view_name_collisions", lambda *a, **k: None)

    async def _fake_create_project_compat(request_dict):
        return {"id": "should-not-get-here"}

    monkeypatch.setattr(pc, "create_project_compat", _fake_create_project_compat)
    monkeypatch.setattr(pc, "require_request_user_id", lambda request: None)

    payload = pc.CreateProjectRequest(
        name="multi-pbix-collision-test",
        source={"type": "pbix", "models": [COLLIDING_A, COLLIDING_B]},
    )

    with pytest.raises(ValidationError, match="Naming collision detected"):
        asyncio.run(pc.create_project(object(), payload))


def test_create_project_allows_distinctly_named_files_through(monkeypatch):
    import semabridge.api.controllers.projects_controller as pc

    called = {}

    async def _fake_create_project_compat(request_dict):
        called["ran"] = True
        return {"id": "proj-ok"}

    monkeypatch.setattr(pc, "create_project_compat", _fake_create_project_compat)
    monkeypatch.setattr(pc, "require_request_user_id", lambda request: None)

    payload = pc.CreateProjectRequest(
        name="multi-pbix-ok-test",
        source={"type": "pbix", "models": [COLLIDING_A, DISTINCT_C]},
    )

    result = asyncio.run(pc.create_project(object(), payload))
    assert result == {"id": "proj-ok"}
    assert called["ran"] is True


# ---------------------------------------------------------------------------
# 4. dry-run controller boundary (fail-fast symmetry with create_project)
# ---------------------------------------------------------------------------


def test_dry_run_mapping_rejects_colliding_selected_sources_before_running_pipeline(monkeypatch):
    import semabridge.api.controllers.mappings_controller as mc
    import semabridge.api.services.core_domain_service as core_domain_service

    called = {"sync_models": False}

    async def _fake_sync_models(payload):
        called["sync_models"] = True
        return {"status": "success", "results": []}

    monkeypatch.setattr(core_domain_service, "sync_models", _fake_sync_models)
    monkeypatch.setattr(mc, "require_request_user_id", lambda request: None)
    monkeypatch.setattr(mc, "validate_project_connector_accounts_belong_to_user", lambda *a, **k: None)

    request = mc.DryRunRequest(
        source_config={"type": "pbix"},
        target_config={"type": "snowflake"},
        selected_sources=[COLLIDING_A, COLLIDING_B],
    )

    class _FakeService:
        pass

    import json

    response = asyncio.run(mc.dry_run_mapping("preview", object(), request, service=_FakeService()))

    assert called["sync_models"] is False
    assert response.status_code == 400
    body = json.loads(response.body)
    assert body["success"] is False
    assert "Naming collision detected" in body["error"]
