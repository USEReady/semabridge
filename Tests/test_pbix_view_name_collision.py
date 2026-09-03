"""Multi-PBIX naming and collision-*disambiguation* tests (Part D).

Covers:
  - get_pbix_deployment_view_name() / find_pbix_view_name_collisions() (pure logic)
  - resolve_pbix_deployment_base_names(): the actual collision-handling
    mechanism today. Two files that would otherwise produce the identical
    clean base name are auto-disambiguated with a short "_2"/"_3" suffix
    instead of being blocked with a validation error -- consistent with
    this codebase's usual collision-handling shape (see
    dimensions_clause_builder.py's alias _2/_3 fallback): try the clean
    name first, only disambiguate when a genuine collision is detected.
  - Both former "early feedback" enforcement points (project-configuration
    time in projects_controller.create_project, and dry-run time in
    mappings_controller.dry_run_mapping) no longer raise on a naming
    collision -- disambiguation now happens once, at job-construction time
    (_build_sync_jobs, shared by dry-run AND deploy), so a colliding upload
    is never rejected outright.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

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

from semabridge.utils.name_translator import (
    find_pbix_view_name_collisions,
    get_pbix_deployment_view_name,
    resolve_pbix_deployment_base_names,
)

COLLIDING_A = "C:/Reports/Sales Report.pbix"
COLLIDING_B = "C:/Reports/Sales_Report.pbix"
DISTINCT_C = "C:/Reports/Marketing Analysis.pbix"


# ---------------------------------------------------------------------------
# 1. Pure naming/collision logic
# ---------------------------------------------------------------------------


def test_get_pbix_deployment_view_name_derives_from_filename_not_project():
    assert get_pbix_deployment_view_name("C:/Reports/Sales Report.pbix", "snowflake") == "SALES_REPORT_SEMANTIC"


def test_get_pbix_deployment_view_name_strips_upload_uuid_hash_prefix():
    # pbix_service.py's _save_uploaded_pbix_file stores every upload as
    # "{uuid4().hex}_{original filename}" -- that prefix must never leak
    # into the deployed view name.
    name = get_pbix_deployment_view_name(
        "C:/tmp/f2556718378448fc91e0db143cdf6fb7_Customer Profitability.pbix", "snowflake"
    )
    assert name == "CUSTOMER_PROFITABILITY_SEMANTIC"


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


# ---------------------------------------------------------------------------
# 2. resolve_pbix_deployment_base_names(): disambiguate, don't block
# ---------------------------------------------------------------------------


def test_resolve_disambiguates_colliding_names_with_short_numeric_suffix():
    resolved = resolve_pbix_deployment_base_names([COLLIDING_A, COLLIDING_B])
    assert resolved[COLLIDING_A] == "SALES_REPORT"
    assert resolved[COLLIDING_B] == "SALES_REPORT_2"
    # Never a long UUID/hash fallback.
    assert len(resolved[COLLIDING_B]) < len("SALES_REPORT") + 40


def test_resolve_disambiguation_order_follows_input_order_not_alphabetical():
    # Whichever file is listed FIRST keeps the clean, unsuffixed name.
    resolved_b_first = resolve_pbix_deployment_base_names([COLLIDING_B, COLLIDING_A])
    assert resolved_b_first[COLLIDING_B] == "SALES_REPORT"
    assert resolved_b_first[COLLIDING_A] == "SALES_REPORT_2"


def test_resolve_leaves_distinct_names_untouched():
    resolved = resolve_pbix_deployment_base_names([COLLIDING_A, DISTINCT_C])
    assert resolved[COLLIDING_A] == "SALES_REPORT"
    assert resolved[DISTINCT_C] == "MARKETING_ANALYSIS"


def test_resolve_handles_three_way_collision():
    third = "C:/Reports/Sales-Report.pbix"
    resolved = resolve_pbix_deployment_base_names([COLLIDING_A, COLLIDING_B, third])
    assert resolved[COLLIDING_A] == "SALES_REPORT"
    assert resolved[COLLIDING_B] == "SALES_REPORT_2"
    assert resolved[third] == "SALES_REPORT_3"


# ---------------------------------------------------------------------------
# 3. Call site 1: _build_sync_jobs() — shared by dry-run AND deploy
# ---------------------------------------------------------------------------


def test_build_sync_jobs_disambiguates_colliding_filenames_instead_of_rejecting(tmp_path):
    from semabridge.api.services import sync_execution_service as ses

    file_a = tmp_path / "Sales Report.pbix"
    file_b = tmp_path / "Sales_Report.pbix"
    file_a.write_bytes(b"a")
    file_b.write_bytes(b"b")

    source_cfg = {"type": "pbix", "models": [str(file_a), str(file_b)]}
    sync_jobs, *_ = ses._build_sync_jobs({"source": source_cfg, "targets": [{"type": "snowflake"}]})

    assert len(sync_jobs) == 2
    resolved_by_path = {job["pbix_path"]: job["resolved_display_name"] for job in sync_jobs}
    assert resolved_by_path[str(file_a)] == "SALES_REPORT"
    assert resolved_by_path[str(file_b)] == "SALES_REPORT_2"


def test_build_sync_jobs_collision_handling_is_independent_of_dry_run_flag(tmp_path):
    """_build_sync_jobs() takes no dry_run parameter at all -- it's the exact
    same job list (and therefore the exact same disambiguation) regardless
    of whether the caller (execute_sync_request) is about to deploy or only
    dry-run. There is no separate code path for this to drift out of sync
    between the two.
    """
    from semabridge.api.services import sync_execution_service as ses

    file_a = tmp_path / "Sales Report.pbix"
    file_b = tmp_path / "Sales_Report.pbix"
    file_a.write_bytes(b"a")
    file_b.write_bytes(b"b")

    source_cfg = {"type": "pbix", "models": [str(file_a), str(file_b)]}
    config = {"source": source_cfg, "targets": [{"type": "snowflake"}]}

    sync_jobs, *_ = ses._build_sync_jobs(config)
    resolved_by_path = {job["pbix_path"]: job["resolved_display_name"] for job in sync_jobs}
    assert resolved_by_path[str(file_a)] == "SALES_REPORT"
    assert resolved_by_path[str(file_b)] == "SALES_REPORT_2"


# ---------------------------------------------------------------------------
# 4. Project-configuration time (create_project) no longer blocks on naming
# ---------------------------------------------------------------------------


def test_create_project_no_longer_rejects_colliding_filenames(monkeypatch):
    import semabridge.api.controllers.projects_controller as pc

    called = {"create_project_compat": False}

    async def _fake_create_project_compat(request_dict):
        called["create_project_compat"] = True
        return {"id": "proj-ok"}

    monkeypatch.setattr(pc, "create_project_compat", _fake_create_project_compat)
    monkeypatch.setattr(pc, "require_request_user_id", lambda request: None)

    payload = pc.CreateProjectRequest(
        name="multi-pbix-collision-test",
        source={"type": "pbix", "models": [COLLIDING_A, COLLIDING_B]},
    )

    result = asyncio.run(pc.create_project(object(), payload))
    assert result == {"id": "proj-ok"}
    assert called["create_project_compat"] is True


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
# 5. dry-run controller boundary no longer blocks on naming either
# ---------------------------------------------------------------------------


def test_dry_run_mapping_no_longer_rejects_colliding_selected_sources(monkeypatch):
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

    asyncio.run(mc.dry_run_mapping("preview", object(), request, service=_FakeService()))

    assert called["sync_models"] is True
