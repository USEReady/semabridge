"""Phase-1 sync safety fixes: collisions block, demo drift never touches
real data, drift proposals come from the real translator, duplicate-sync
overlap, and the stale-run sweep spares in-flight runs."""

import json

from semabridge.api.services import duplicate_sync_service as dup
from semabridge.api.services import run_service
from semabridge.api.services.mapping_service import _compat_is_blocking_mapping
from semabridge.governance import drift_service


def test_unresolved_name_collision_blocks_even_when_status_is_auto():
    row = {"target_name": "STATE", "status": "auto", "validation_code": "NAME_COLLISION",
           "validation_message": "Name collision detected.", "collision_detected": True}
    assert _compat_is_blocking_mapping(row) is True
    resolved = {**row, "validation_message": "Collision resolved with suffix", "collision_detected": False}
    assert _compat_is_blocking_mapping(resolved) is False


def test_demo_seeding_keeps_real_events_and_never_overrides_a_real_metric(tmp_path, monkeypatch):
    state_file = tmp_path / "drift_state.json"
    monkeypatch.setattr(drift_service, "_state_path", lambda: state_file)
    real = [
        {"id": "real-1", "project_id": "proj-regional-sales", "metric_name": "REVENUE", "status": "pending",
         "proposed_snowflake_definition": "SUM(X)"},
        {"id": "real-2", "project_id": "proj-comp", "metric_name": "TOTAL_UNITS", "status": "pending",
         "proposed_snowflake_definition": "SUM(Y)"},
    ]
    state_file.write_text(json.dumps({"schedules": [], "events": real}))

    events = drift_service.seed_mock_drift_events("proj-comp")
    events_again = drift_service.seed_mock_drift_events("proj-comp")

    stored = json.loads(state_file.read_text())["events"]
    ids = {e["id"] for e in stored}
    assert {"real-1", "real-2"} <= ids  # "sales" projects are no longer wiped
    total_units = [e for e in events if e["metric_name"] == "TOTAL_UNITS"]
    assert [e["id"] for e in total_units] == ["real-2"]  # real wins over demo
    assert all(e.get("mock") for e in events if e["id"] not in {"real-2"})
    assert len(events_again) == len(events)  # re-seeding replaces, never duplicates


def test_real_drift_proposal_uses_translator_not_name_keyed_demo_sql():
    assert drift_service.propose_snowflake_sql("Customer_Count_By_Product", "COUNT(CUSTOMER.ID)") == "COUNT(CUSTOMER.ID)"
    dax = drift_service.propose_snowflake_sql("Total Units", "CALCULATE(SUM(SalesFact[Units]))")
    assert "UNITS" in dax.upper() and "PRODUCTID" not in dax.upper()
    assert drift_service.propose_snowflake_sql("MARGIN_PCT", "", mock=False) == ""
    assert "SALESFACT" in drift_service.propose_snowflake_sql("MARGIN_PCT", "x", mock=True)


def test_same_content_needs_most_fields_shared():
    mine = {"metrics": [{"unique_name": f"M{i}"} for i in range(10)],
            "datasets": [{"unique_name": "T", "columns": [{"unique_name": "A"}, {"unique_name": "B"}]}]}
    m1, c1 = dup._blob_fields(mine)
    same = dup._is_same_content(dup._overlap(m1, set(m1)), dup._overlap(c1, set(c1)))
    different = dup._is_same_content(dup._overlap(m1, {"M0"}), dup._overlap(c1, set()))
    assert same is True and different is False


def test_original_metric_names_are_compared_not_renamed_labels():
    metrics, _ = dup._blob_fields({"metrics": [{"unique_name": "SALES_TOTAL", "original_name": "Total Sales"}]})
    assert metrics == {"TOTAL_SALES"}


def test_in_flight_run_is_recognised_for_the_sweep():
    """Liveness is tracked per run id (heartbeat registry), and the sweep
    spares a live run's per-model rows through their parent_run_id."""
    from semabridge.core import run_coordination

    run_coordination.register_live_run("run-x")
    try:
        assert run_service._is_run_active("run-x") is True
        assert run_service._is_run_active("run-y") is False
    finally:
        run_coordination.unregister_live_run("run-x")
    assert run_service._is_run_active("run-x") is False
