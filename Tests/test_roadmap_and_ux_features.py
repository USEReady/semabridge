"""Tests for Items 1, 2, 7, 8, 9 and Roadmap Features 1, 3, 4, 5."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient


def test_glossary_suggestion_in_pre_dry_run_analysis():
    from semabridge.ontology.quality import SourceMetric, analyze_source_metrics

    res = analyze_source_metrics([
        SourceMetric(name="Total_Revenue", label="Total Revenue", expression="SUM(Sales[Revenue])", source="Sales.pbix"),
    ])
    gaps = res["glossary_gaps"]
    assert len(gaps) == 1
    assert gaps[0]["suggested_description"]
    assert "Total sum" in gaps[0]["suggested_description"]


def test_evidence_pack_pdf_and_xlsx_generation():
    from semabridge.reconciliation.evidence_pack import build_evidence_pdf, build_evidence_xlsx

    records = [{
        "metric_name": "COUNT_OF_PRODUCT",
        "status": "CERTIFIED",
        "dax_row_count": 5,
        "sql_row_count": 5,
        "max_percentage_delta": 0.0,
        "certification_badge": "CERTIFIED_PARITY",
        "approved_by": "qa_lead",
        "approved_at": "2026-11-12T10:00:00Z",
    }]
    pdf_bytes = build_evidence_pdf(records, audit_trail_id="AUD-TEST1234", project_id="proj-dfv", approver="qa_lead")
    assert pdf_bytes.startswith(b"%PDF-1.4")
    assert b"AUD-TEST1234" in pdf_bytes

    xlsx_bytes = build_evidence_xlsx(records, audit_trail_id="AUD-TEST1234", project_id="proj-dfv", approver="qa_lead")
    with zipfile.ZipFile(io.BytesIO(xlsx_bytes), "r") as zf:
        names = set(zf.namelist())
        assert "xl/workbook.xml" in names
        assert "xl/worksheets/sheet1.xml" in names
        assert "xl/worksheets/sheet2.xml" in names
        assert b"AUD-TEST1234" in zf.read("xl/worksheets/sheet1.xml")


def test_reverse_sync_additive_only_on_tmdl(tmp_path: Path):
    from semabridge.sync.reverse_sync import apply_reverse_upgrades, preview_reverse_upgrades

    tmdl = tmp_path / "Sales.tmdl"
    tmdl.write_text("table Sales\n\tmeasure 'Existing Measure' = SUM(Sales[Amount])\n", encoding="utf-8")

    res = apply_reverse_upgrades(
        pbip_path=str(tmp_path),
        upgrades=[
            {"action": "ADD_MEASURE", "metric_name": "Existing Measure", "expression": "0"},
            {"action": "ADD_MEASURE", "metric_name": "Net Margin", "expression": "SUM(Sales[Margin])", "description": "Net margin"},
        ],
    )
    assert res["applied"] == ["Net Margin"]
    assert res["skipped"] == ["Existing Measure"]
    updated = tmdl.read_text(encoding="utf-8")
    assert "measure 'Existing Measure' = SUM(Sales[Amount])" in updated
    assert "measure 'Net Margin' = SUM(Sales[Margin])" in updated

    prev = preview_reverse_upgrades(pbip_path=str(tmp_path))
    assert prev["existing_bi_measures"] == 2


def test_cross_platform_and_api_endpoints():
    from semabridge.api.main import app

    client = TestClient(app)
    r1 = client.post("/api/cross-platform/preview", json={"version_tag": "v2.1.0"})
    assert r1.status_code == 200
    body1 = r1.json()
    assert set(body1["previews"].keys()) == {"snowflake", "databricks", "fabric"}

    r2 = client.post("/api/drift/check", json={})
    assert r2.status_code == 200
    assert "checked_metrics" in r2.json()

    r3 = client.get("/api/reconciliation/evidence-pack?format=xlsx")
    assert r3.status_code == 200
    assert "spreadsheetml" in r3.headers["content-type"]

    r4 = client.post("/api/bi-truth/generate", json={})
    assert r4.status_code == 200
    assert "added_count" in r4.json()

    r5 = client.post(
        "/api/ontology/review/snowflake-similarity",
        json={"metrics": [{"name": "KPI01", "label": "KPI01", "expression": "SUM(Sales[Amount])"}]},
    )
    assert r5.status_code == 200
    assert len(r5.json()["matches"]) == 1


def test_dynamic_scoped_table_isolation_on_partial_schema_drift():
    from Tests.test_table_binding_determinism import _Conn, _Cursor, _schema_manager, clear_schema_cache
    from semabridge.sml.models import SMLColumn, SMLDataset, SMLModel

    clear_schema_cache()
    # 7/9 = 77.8% compatibility with existing SENTIMENT table
    live_cols = ["DATE_ID", "STATE_ID", "MANUFACTURER_ID", "SCORE", "MANUFACTURER", "DATE", "STATE", "EXTRA_A", "EXTRA_B"]
    conn = _Conn(tables=["SENTIMENT"], columns={"SENTIMENT": live_cols}, row_counts={"SENTIMENT": 100})
    manager = _schema_manager(conn)
    pbix_cols = [SMLColumn(unique_name=c) for c in ["DATE_ID", "STATE_ID", "MANUFACTURER_ID", "SCORE", "MANUFACTURER", "DATE", "STATE", "ZIP", "PRODUCT_ID"]]
    model = SMLModel(unique_name="dfv", datasets=[SMLDataset(unique_name="Sentiment", columns=pbix_cols)])
    plan = {e["dataset"]: e for e in manager.plan_source_tables(_Cursor(), model)}
    assert plan["Sentiment"]["action"] == "create"
    assert plan["Sentiment"]["table"] == "DFV_SENTIMENT"
    clear_schema_cache()


def test_unrelated_zero_name_score_column_ties_do_not_block():
    from Tests.test_table_binding_determinism import LiveSchemaMetadata
    from semabridge.connectors.table_binding_resolver import resolve_dataset_to_physical_table

    history_cols = {"ID", "FIELD", "OLDVALUE", "NEWVALUE", "CREATEDDATE"}
    meta = LiveSchemaMetadata({
        "REP_SFDC_CUSTOMER_PROJECT_HISTORY_LATEST": history_cols,
        "REP_SFDC_CUSTOMER_SOLUTIONS_HISTORY_LATEST": history_cols,
        "REP_SFDC_SBQQ_QUOTE_HISTORY_LATEST": history_cols,
    })
    resolved = resolve_dataset_to_physical_table(
        "REP_SFDC_OPPORTUNITYFIELDHISTORY",
        ["ID", "FIELD", "OLDVALUE", "NEWVALUE", "CREATEDDATE"],
        meta,
    )
    assert resolved == "REP_SFDC_OPPORTUNITYFIELDHISTORY"


def test_reconciliation_project_metrics_and_update_sql():
    from fastapi.testclient import TestClient
    from semabridge.api.main import app
    from semabridge.ontology.store import ontology_store
    from semabridge.ontology.models import DomainOntology, DomainMetric

    # Register a domain with DAX and SQL expressions
    dom = DomainOntology(
        domain="ParityTestDomain",
        source_project_id="proj-parity-test",
        metrics=[
            DomainMetric(
                name="TotalUnitsParity",
                dax_expression="SUM(Sales[Units])",
                sql_expression='SUM("SALES"."UNITS")',
            )
        ],
    )
    ontology_store.add_or_update_domain(dom)

    client = TestClient(app)

    # 1. Test GET /api/reconciliation/project-metrics
    resp = client.get("/api/reconciliation/project-metrics?project_id=proj-parity-test")
    assert resp.status_code == 200
    metrics = resp.json().get("metrics", [])
    found = next((m for m in metrics if m["name"] == "TotalUnitsParity"), None)
    assert found is not None
    assert found["dax_expression"] == "SUM(Sales[Units])"
    assert found["sql_expression"] == 'SUM("SALES"."UNITS")'

    # 2. Test POST /api/reconciliation/update-metric-sql
    update_resp = client.post(
        "/api/reconciliation/update-metric-sql",
        json={
            "project_id": "proj-parity-test",
            "metric_name": "TotalUnitsParity",
            "sql_expression": 'SUM(COALESCE("SALES"."UNITS", 0))',
        },
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["sql_expression"] == 'SUM(COALESCE("SALES"."UNITS", 0))'

    # Verify updated in store
    updated_dom = ontology_store.get_domain("ParityTestDomain")
    updated_metric = next(m for m in updated_dom.metrics if m.name == "TotalUnitsParity")
    assert updated_metric.sql_expression == 'SUM(COALESCE("SALES"."UNITS", 0))'
    assert updated_metric.dax_expression == "SUM(Sales[Units])"


