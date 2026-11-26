"""Databricks-aware wording/behaviour; Snowflake text stays unchanged."""
import asyncio

import pytest
from fastapi import HTTPException

from semabridge.api.routers import domain_ontology_router as dor
from semabridge.api.services import project_mapping_engine as pme
from semabridge.api.services import run_report_service as rrs
from semabridge.api.services import run_service


def _label(states):
    return {s["id"]: s["label"] for s in states}["snowflake_deployment"]


def test_stage_label_databricks_keeps_id():
    states = run_service._build_stage_states({"summary": {"target_type": "databricks"}})
    assert _label(states) == "Databricks Deployment"
    assert [s["id"] for s in states][-1] == "snowflake_deployment"


@pytest.mark.parametrize("sync", [{}, {"summary": {"target_type": "snowflake"}}, {"summary": {}}])
def test_stage_label_snowflake_unchanged(sync):
    assert _label(run_service._build_stage_states(sync)) == "Snowflake Deployment"


OUT = {"converted": 1, "expected_count": 2, "unexpected_count": 0, "expected": [], "expected_by_reason": []}


def test_outcomes_markdown_wording():
    sf = "\n".join(rrs._metric_outcomes_markdown(OUT))
    assert "by design — Snowflake limitation" in sf
    assert sf == "\n".join(rrs._metric_outcomes_markdown(OUT, "snowflake"))
    db = "\n".join(rrs._metric_outcomes_markdown(OUT, "databricks"))
    assert "by design — target platform limitation" in db and "Snowflake" not in db


def test_live_schema_reason_rules_databricks():
    for text in ("not found in the live databricks schema", "no live databricks schema was fetched"):
        rec = {"reason": f"x {text}", "stage": "schema_validation"}
        assert "Databricks" not in rrs.humanize_drop_reason(rec)
        assert "database" in rrs.humanize_drop_reason(rec)


def _rows():
    def col(table, name):
        return {"entity_kind": "column", "model_name": "m", "parent_source_path": f"datasets.{table}",
                "target_name": name, "sanitized_name": name}
    return [col("customer", "name"), col("product", "name")]


def test_hidden_collision_note_databricks():
    rows = _rows()
    pme._flag_hidden_collisions(rows, "databricks")
    note = rows[0]["advisory_notes"][0]
    assert note == (
        "name also exists in product. In a Databricks metric view all names share one list, "
        "so the planner names one of them customer_name."
    )
    assert "NAME" not in note and "deploys fine" not in note


def test_hidden_collision_note_snowflake_unchanged():
    rows = _rows()
    pme._flag_hidden_collisions(rows, "snowflake")
    assert rows[0]["advisory_notes"][0] == (
        "NAME also exists in product. It deploys fine as CUSTOMER.NAME, but a bare NAME "
        "is ambiguous when the view is queried without the table name."
    )


def test_hidden_metric_collision_text():
    def rows():
        return [
            {"entity_kind": "column", "model_name": "m", "parent_source_path": "datasets.sales",
             "target_name": "region", "sanitized_name": "region"},
            {"entity_kind": "metric", "model_name": "m", "home_dataset": "sales",
             "target_name": "region", "sanitized_name": "region"},
        ]
    d = rows()
    pme._flag_hidden_collisions(d, "databricks")
    msg = d[1]["validation_message"]
    assert "named region in Databricks" in msg and "metric view" in msg and "semantic view" not in msg
    s = rows()
    pme._flag_hidden_collisions(s, "snowflake")
    assert "named REGION in Snowflake" in s[1]["validation_message"]
    assert "semantic view" in s[1]["validation_message"]


def test_kg_sql_export_409_for_databricks(monkeypatch):
    monkeypatch.setattr(dor, "_is_databricks_project", lambda pid: True)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(dor.export_project_knowledge_graph_sql("p1"))
    assert ei.value.status_code == 409
    assert ei.value.detail == dor.SNOWFLAKE_ONLY_REASON


def test_similarity_not_available_for_databricks(monkeypatch):
    monkeypatch.setattr(dor, "_is_databricks_project", lambda pid: True)
    monkeypatch.setattr(dor, "_review_access", lambda request, pid: None)
    out = dor.check_snowflake_metric_similarity(dor.SnowflakeSimilarityRequest(project_id="p1"), object())
    assert out == {"available": False, "reason": "Snowflake only: not available for Databricks projects"}


def test_is_databricks_project_reads_stored_type(monkeypatch):
    from semabridge.api.services import project_shared

    monkeypatch.setattr(project_shared, "_compat_stored_target_type", lambda pid: "databricks")
    assert dor._is_databricks_project("p") is True
    monkeypatch.setattr(project_shared, "_compat_stored_target_type", lambda pid: "snowflake")
    assert dor._is_databricks_project("p") is False
    assert dor._is_databricks_project(None) is False


@pytest.fixture(autouse=True)
def _databricks_planner_on(monkeypatch):
    """These tests describe the Databricks planner path (naming v2). The
    planner switch (behavior.databricks.use_planner_path) is off by default,
    so turn it on for them."""
    from semabridge.target_rules import naming

    monkeypatch.setattr(naming, "databricks_planner_on", lambda: True)
    try:
        import semabridge.api.services.project_mapping_engine as pme

        monkeypatch.setattr(pme, "databricks_planner_on", lambda: True)
    except Exception:  # noqa: BLE001
        pass
