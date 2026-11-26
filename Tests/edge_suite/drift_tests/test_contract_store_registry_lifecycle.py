"""4 to 7. Baseline store, registry and connections, cache, event lifecycle.

These parts own state and talk to the warehouse, so the cases are written
against the contract rather than against a reference implementation. They run
against the product with ``SB_DRIFT_IMPL=product``; otherwise they are reported
as not run, because passing them without the product would prove nothing.
"""
from __future__ import annotations

import pytest

from drift_tests import adapter

pytestmark = pytest.mark.skipif(
    not adapter.is_product,
    reason="stateful contract: run with SB_DRIFT_IMPL=product against the product modules",
)


def store():
    import importlib, os  # noqa: E401
    module = os.environ.get("SB_DRIFT_STORE_MODULE",
                            "semabridge.governance.drift.baseline_store")
    return importlib.import_module(module)


def registry():
    import importlib, os  # noqa: E401
    module = os.environ.get("SB_DRIFT_REGISTRY_MODULE",
                            "semabridge.governance.drift.adapters.registry")
    return importlib.import_module(module)


# ---------------------------------------------------------------- 4. baseline store

def test_4_1_baseline_saved_at_deploy_is_the_text_that_executed(deployed_project):
    saved = store().get_active(deployed_project.id, deployed_project.view)
    assert saved.definition_text.strip() == deployed_project.executed_text.strip()


def test_4_2_objects_dropped_by_remediation_are_recorded_and_never_deleted(partial_deploy):
    saved = store().get_active(partial_deploy.id, partial_deploy.view)
    assert partial_deploy.rejected_object in saved.not_deployed


def test_4_3_a_failed_deploy_leaves_the_baseline_unchanged(failed_deploy):
    assert (store().get_active(failed_deploy.id, failed_deploy.view).definition_text
            == failed_deploy.previous_text)


def test_4_4_a_failed_baseline_write_does_not_fail_the_deploy(deploy_with_store_down):
    assert deploy_with_store_down.deploy_succeeded


def test_4_5_latest_baseline_is_active_and_history_keeps_both(two_deploys):
    history = store().history(two_deploys.id, two_deploys.view)
    assert len(history) >= 2
    assert store().get_active(two_deploys.id, two_deploys.view).deployed_at == history[0].deployed_at


def test_4_7_same_short_view_name_in_two_schemas_never_collides(two_schemas):
    first = store().get_active(two_schemas.id, two_schemas.view_a)
    second = store().get_active(two_schemas.id, two_schemas.view_b)
    assert first.definition_text != second.definition_text


def test_4_9_no_baseline_gives_an_explicit_reason_not_a_comparison(project_without_baseline):
    result = project_without_baseline.run_check()
    assert result["status"] == "NOT_CHECKED"
    assert result["reason_code"] == "NO_DEPLOY_BASELINE"
    assert not result.get("events")


def test_4_11_a_baseline_written_by_one_process_is_read_by_another(second_process):
    assert second_process.read_active() is not None


def test_4_12_clearing_history_removes_events_only(project_with_events):
    project_with_events.clear_history()
    assert project_with_events.events() == []
    assert store().get_active(project_with_events.id, project_with_events.view) is not None


def test_4_13_no_local_state_file_is_written_in_normal_running(normal_run):
    assert normal_run.state_files_written == []


# ---------------------------------------------------------------- 5. registry and connections

def test_5_1_adapter_comes_from_the_project_platform_field(project_of_each_platform):
    for project in project_of_each_platform:
        assert registry().get_adapter(project.platform, "live").platform == project.platform


def test_5_2_unknown_platform_gives_a_clear_status_not_an_exception():
    result = registry().resolve("a-platform-that-does-not-exist", "live")
    assert result.supported is False and result.reason_code == "PLATFORM_NOT_SUPPORTED"


def test_5_3_mock_is_refused_when_the_backend_does_not_allow_it(mock_disabled):
    result = registry().resolve(mock_disabled.platform, "mock")
    assert result.supported is False


def test_5_4_fixture_adapter_reads_from_the_configured_folder(fixture_mode):
    assert fixture_mode.folder_used == fixture_mode.configured_folder
    assert "test" not in str(fixture_mode.folder_used).lower().split("/")[-2:]


@pytest.mark.parametrize("failure,expected", [
    ("auth", "WAREHOUSE_AUTH_FAILED"),
    ("permission", "WAREHOUSE_AUTH_FAILED"),
    ("timeout", "WAREHOUSE_UNREACHABLE"),
    ("dropped", "WAREHOUSE_UNREACHABLE"),
    ("view_missing", "VIEW_NOT_FOUND"),
    ("truncated", "PARSE_ERROR"),
])
def test_5_6_warehouse_failures_each_give_their_own_reason(warehouse_failure, failure, expected):
    result = warehouse_failure(failure).run_check()
    assert result["status"] == "NOT_CHECKED" and result["reason_code"] == expected
    assert not result.get("events")


def test_5_12_view_names_come_from_deployment_records_not_the_request(request_with_other_view):
    result = request_with_other_view.run_check()
    assert result["status"] == "NOT_CHECKED"


# ---------------------------------------------------------------- 6. concurrency and cache

def test_6_1_a_second_check_for_the_same_view_returns_in_progress(concurrent_checks):
    first, second = concurrent_checks.run_together()
    assert "CHECK_IN_PROGRESS" in (first.get("reason_code"), second.get("reason_code"))


def test_6_2_each_schedule_runs_once_with_two_processes(two_schedulers):
    assert two_schedulers.run_count == 1


def test_6_3_schedules_survive_a_restart(restarted_server):
    assert restarted_server.schedules_after == restarted_server.schedules_before


def test_6_8_two_checks_inside_the_ttl_make_one_warehouse_call(cached_project):
    cached_project.run_check()
    cached_project.run_check()
    assert cached_project.warehouse_calls == 1


def test_6_9_after_the_ttl_the_warehouse_is_read_again(cached_project):
    cached_project.run_check()
    cached_project.advance_beyond_ttl()
    cached_project.run_check()
    assert cached_project.warehouse_calls == 2


def test_6_10_force_refresh_bypasses_the_cache(cached_project):
    cached_project.run_check()
    cached_project.run_check(force_refresh=True)
    assert cached_project.warehouse_calls == 2


def test_6_11_a_deploy_invalidates_the_cache(cached_project):
    cached_project.run_check()
    cached_project.deploy()
    cached_project.run_check()
    assert cached_project.warehouse_calls == 2


def test_6_13_two_projects_on_the_same_view_name_do_not_share_cache_entries(twin_projects):
    twin_projects.first.run_check()
    twin_projects.second.run_check()
    assert twin_projects.warehouse_calls == 2


# ---------------------------------------------------------------- 7. events and lifecycle

def test_7_1_the_same_drift_on_repeated_checks_is_one_event(repeating_drift):
    for _ in range(5):
        repeating_drift.run_check()
    assert len(repeating_drift.events()) == 1
    assert repeating_drift.events()[0]["last_seen"] > repeating_drift.events()[0]["first_detected"]


def test_7_2_drift_fixed_in_the_warehouse_closes_the_event(fixed_drift):
    fixed_drift.run_check()
    fixed_drift.revert_warehouse_change()
    fixed_drift.run_check()
    assert fixed_drift.events()[0]["status"] == "auto_resolved"


def test_7_3_an_approved_finding_with_the_same_value_stays_closed(approved_finding):
    approved_finding.run_check()
    assert approved_finding.events()[0]["status"] == "accepted"


def test_7_4_an_approved_finding_with_a_new_value_reopens(approved_finding):
    approved_finding.change_warehouse_again()
    approved_finding.run_check()
    assert approved_finding.events()[0]["status"] == "pending"


def test_7_5_every_action_needs_a_reason_and_permission(reviewer_actions):
    for action in reviewer_actions.all_actions:
        assert reviewer_actions.without_reason(action).refused
        assert reviewer_actions.without_permission(action).refused


def test_7_7_paging_returns_each_event_exactly_once(many_events):
    seen = []
    for page in many_events.pages(page_size=25):
        seen.extend(event["id"] for event in page)
    assert len(seen) == len(set(seen)) == many_events.total


def test_7_10_counts_in_the_response_match_the_stored_events(many_events):
    counts = many_events.response_counts()
    assert sum(sum(c.values()) for c in counts.values()) == many_events.total
