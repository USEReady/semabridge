"""Tests for automated DAX execution backends, metric certifications, Cortex gating, and AI model blast radius."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from semabridge.api.main import app
from semabridge.governance.blast_radius import BlastRadiusSimulator
from semabridge.governance.linter import SemanticLinter
from semabridge.ontology.federated_loader import FederatedOntologyLoader
from semabridge.reconciliation.certification_service import (
    check_sql_certification_status,
    get_failed_certifications,
    get_latest_certifications,
    save_certification_record,
)
from semabridge.reconciliation.dax_executor import (
    discover_local_pbi_ports,
    normalize_dax_column_names,
    run_dax,
)
from semabridge.repository.orm.ai_models import MetricCertification, QueryExecutionHistory, VerifiedQuery
from semabridge.repository.orm.session_factory import db_manager


def test_normalize_dax_column_names():
    cols = normalize_dax_column_names(["'Cases'[Agent]", "[Case Count]", "PlainCol"])
    assert cols == ["Agent", "Case Count", "PlainCol"]

    dup_cols = normalize_dax_column_names(["'Cases'[Status]", "'Opportunities'[Status]"])
    assert dup_cols == ["'Cases'[Status]", "'Opportunities'[Status]"]


def test_discover_local_pbi_ports_utf16(tmp_path, monkeypatch):
    ws_data = tmp_path / "AnalysisServicesWorkspaces" / "AnalysisServicesWorkspace123" / "Data"
    ws_data.mkdir(parents=True)
    (ws_data / "msmdsrv.port.txt").write_bytes("54321\r\n".encode("utf-16-le"))

    monkeypatch.setattr(
        "semabridge.reconciliation.dax_executor._PBI_PORT_PATTERNS",
        (str(tmp_path / "AnalysisServicesWorkspaces" / "*" / "Data" / "msmdsrv.port.txt"),),
    )
    ports = discover_local_pbi_ports()
    assert len(ports) == 1
    assert ports[0]["port"] == 54321


def test_run_dax_pbi_desktop_and_xmla_mocked():
    with patch(
        "semabridge.reconciliation.dax_executor._run_adomd_query",
        return_value=(["Agent", "Case Count"], [{"Agent": "Alice", "Case Count": 42}]),
    ) as mock_adomd:
        rows_desktop = run_dax("EVALUATE ROW(\"Case Count\", [Case Count])", backend="pbi_desktop", port=51234)
        assert rows_desktop == [{"Agent": "Alice", "Case Count": 42}]
        assert "Data Source=localhost:51234" in mock_adomd.call_args[0][0]

        rows_xmla = run_dax(
            "EVALUATE ROW(\"Case Count\", [Case Count])",
            backend="xmla",
            workspace="FinanceWS",
            dataset_id="AISample",
            tenant_id="t-1",
            client_id="c-1",
            client_secret="sec-1",
        )
        assert rows_xmla == [{"Agent": "Alice", "Case Count": 42}]
        assert "powerbi://api.powerbi.com/v1.0/myorg/FinanceWS" in mock_adomd.call_args[0][0]
        assert "Initial Catalog=AISample" in mock_adomd.call_args[0][0]


def test_run_dax_rest_api_mocked():
    mock_resp = MagicMock()
    mock_resp.is_success = True
    mock_resp.content = b"{}"
    mock_resp.json.return_value = {
        "results": [
            {
                "tables": [
                    {
                        "rows": [
                            {"Cases[Agent]": "Sanjay Shah", "[Case Count]": 701},
                        ]
                    }
                ]
            }
        ]
    }
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.post.return_value = mock_resp

    with (
        patch("semabridge.reconciliation.dax_executor._acquire_powerbi_token", return_value="tok-abc"),
        patch("semabridge.reconciliation.dax_executor.httpx.Client", return_value=mock_client),
    ):
        rows = run_dax(
            "EVALUATE SUMMARIZECOLUMNS('Cases'[Agent], \"Case Count\", [Case Count])",
            backend="rest_api",
            dataset_id="ds-999",
            workspace_id="ws-111",
        )
    assert rows == [{"Agent": "Sanjay Shah", "Case Count": 701}]


def test_certification_persistence_and_verified_query_gating():
    proj = f"test-cert-{uuid.uuid4().hex[:8]}"
    try:
        save_certification_record(
            metric_name="Case Count",
            view_metric="CASES.CASE_COUNT",
            status="CERTIFIED",
            certification_badge="Certified 100% Match",
            project_id=proj,
            dimensions=["CASES.AGENT"],
            matched_rows=10,
        )
        save_certification_record(
            metric_name="Cases MoM%",
            view_metric="CASES.CASES_MOM_PCT",
            status="FAILED",
            certification_badge="Reconciliation Failed",
            project_id=proj,
            mismatched_rows=5,
        )

        certs = get_latest_certifications(project_id=proj)
        assert {c["metric_name"] for c in certs} == {"Case Count", "Cases MoM%"}
        failed = get_failed_certifications(project_id=proj)
        assert [f["metric_name"] for f in failed] == ["Cases MoM%"]

        ok_chk = check_sql_certification_status(
            "SELECT * FROM SEMANTIC_VIEW(DB.SCH.V METRICS CASES.CASE_COUNT DIMENSIONS CASES.AGENT)",
            project_id=proj,
        )
        assert ok_chk["certified"] == ["CASES.CASE_COUNT"]
        assert not ok_chk["failed"]

        bad_chk = check_sql_certification_status(
            "SELECT * FROM SEMANTIC_VIEW(DB.SCH.V METRICS CASES.CASES_MOM_PCT)",
            project_id=proj,
        )
        assert bad_chk["failed"]

        # Verify API blocks saving failed metric even if SQL execution history exists
        from semabridge.ai.verified_queries_service import record_query_execution

        bad_sql = "SELECT * FROM SEMANTIC_VIEW(DB.SCH.V METRICS CASES.CASES_MOM_PCT)"
        record_query_execution(bad_sql, target="SNOWFLAKE", row_count=1, success=True, is_simulated=False, project_id=proj)

        client = TestClient(app)
        resp = client.post(
            "/api/ai/save-verified-query",
            json={
                "project_id": proj,
                "question": "Monthly cases change?",
                "sql": bad_sql,
                "confirmed": True,
                "target": "SNOWFLAKE",
            },
        )
        assert resp.status_code == 400
        assert "failed" in resp.json()["detail"].lower()
    finally:
        with db_manager.get_session() as s:
            s.query(MetricCertification).filter(MetricCertification.project_id == proj).delete()
            s.query(VerifiedQuery).filter(VerifiedQuery.project_id == proj).delete()
            s.query(QueryExecutionHistory).filter(QueryExecutionHistory.project_id == proj).delete()
            s.commit()


def test_ai_model_drop_col_value_blast_radius_and_recert_remediation():
    ai_path = Path("Tests/fixtures/ontologies/artificial_intelligence_sample.ontology.yaml")
    dom = FederatedOntologyLoader.parse_yaml_string(ai_path.read_text(encoding="utf-8"))
    fed = FederatedOntologyLoader.merge_domains([dom])

    sim = BlastRadiusSimulator(
        ontology=fed,
        verified_queries=[
            {
                "question": "What is our revenue forecast by territory?",
                "sql": "SELECT * FROM SEMANTIC_VIEW(DB.SCH.V METRICS OPPORTUNITIES.FORECAST DIMENSIONS ACCOUNTS.TERRITORY)",
            }
        ],
        certified_metrics={"REVENUE_WON", "FORECAST"},
    )
    report = sim.simulate_impact("OPPORTUNITIES.COL_VALUE", target_type="COLUMN", change_type="DROP")
    by_name = {m.name: m.impact_level for m in report.affected_metrics}

    for direct_m in ("REVENUE_WON", "REVENUE_OPEN", "REVENUE_IN_PIPELINE"):
        assert direct_m in by_name
        assert by_name[direct_m] == "DIRECT"

    for trans_m in ("FORECAST", "FORECAST_PCT", "REV_GOAL"):
        assert trans_m in by_name
        assert by_name[trans_m] == "TRANSITIVE"

    assert any("forecast" in a.name.lower() for a in report.affected_ai_tools)
    assert any("re-certification needed" in step.lower() for step in report.remediation_steps)


def test_linter_per_domain_cycle_isolation_on_real_ontologies():
    files = sorted([
        *Path("Config/ontologies").glob("*.ontology.yaml"),
        *Path("Tests/fixtures/ontologies").glob("*.ontology.yaml"),
    ])
    domains = [FederatedOntologyLoader.parse_yaml_string(f.read_text(encoding="utf-8")) for f in files]
    fed = FederatedOntologyLoader.merge_domains(domains)

    report = SemanticLinter(require_steward=False).lint_ontology(fed)
    assert report.passed is True
    assert report.error_count == 0
    assert not any(i.rule_id == "RULE_CIRCULAR_JOIN" and i.severity == "ERROR" for i in report.issues)


def test_reserved_prefix_helper_strips_only_real_columns():
    from semabridge.utils.reserved_prefixes import strip_reserved_column_prefix

    assert strip_reserved_column_prefix("COL_VALUE", ["Value", "Status"]) == "Value"
    assert strip_reserved_column_prefix("COL_DATE", ["Date", "Year"]) == "Date"
    assert strip_reserved_column_prefix("COL_MISSING", ["Value", "Status"]) is None


def test_reconciliation_normalized_column_mapping_and_validation():
    from semabridge.reconciliation.golden_verifier import GoldenQueryVerifier

    v = GoldenQueryVerifier()
    res = v.reconcile_data(
        metric_name="Case Count",
        dax_rows=[{"Cases[Agent]": "Alice", "Case Count": 12}],
        sql_rows=[{"AGENT": "Alice", "CASE_COUNT": 12}],
        dimension_keys=["CASES.AGENT"],
    )
    assert res.status == "CERTIFIED"
    assert res.column_mapping["metric"]["dax_column"] == "Case Count"
    assert res.column_mapping["metric"]["sql_column"] == "CASE_COUNT"
    assert res.column_mapping["dimensions"][0]["dax_column"] == "Cases[Agent]"
    assert res.column_mapping["dimensions"][0]["sql_column"] == "AGENT"

    # Unrelated column names still produce ERROR (never CERTIFIED)
    bad = v.reconcile_data(
        metric_name="Case Count",
        dax_rows=[{"AGENT": "Alice", "metric_value": 12}],
        sql_rows=[{"AGENT": "Alice", "CASE_COUNT": 12}],
        dimension_keys=["AGENT"],
    )
    assert bad.status == "ERROR"

    # Input validation blocks free text and accepts valid SQL / semantic-view metrics
    client = TestClient(app)
    r_free_sv = client.post(
        "/api/reconciliation/generate-queries",
        json={
            "metric_name": "Case Count",
            "metric_sql_expression": "how many cases do we have",
            "target_table_or_view": "DB.SCH.SV",
            "target_kind": "semantic_view",
            "dimensions": ["CASES.AGENT"],
        },
    )
    assert r_free_sv.status_code == 400

    r_free_tbl = client.post(
        "/api/reconciliation/generate-queries",
        json={
            "metric_name": "Case Count",
            "metric_sql_expression": "hello world",
            "target_table_or_view": "DB.SCH.TBL",
            "dimensions": ["T.A"],
        },
    )
    assert r_free_tbl.status_code == 400

    r_ok_sv = client.post(
        "/api/reconciliation/generate-queries",
        json={
            "metric_name": "Case Count",
            "metric_sql_expression": "CASES.CASE_COUNT",
            "target_table_or_view": "DB.SCH.SV",
            "target_kind": "semantic_view",
            "dimensions": ["CASES.AGENT"],
        },
    )
    assert r_ok_sv.status_code == 200
    assert "METRICS CASES.CASE_COUNT" in r_ok_sv.json()["sql_query"]


def test_same_project_duplicate_domain_warning_formula_diff_and_steward_warning():
    dom1 = FederatedOntologyLoader.parse_yaml_string("""
domain: Alpha
source_project_id: proj-same
metrics:
  - name: M1
    description: Valid metric definition for Alpha
    sql_expression: "SUM( x )"
""")
    dom2 = FederatedOntologyLoader.parse_yaml_string("""
domain: Alpha Copy
source_project_id: proj-same
metrics:
  - name: M1
    description: Valid metric definition for Alpha Copy
    sql_expression: "COUNT(y)"
""")
    fed_same = FederatedOntologyLoader.merge_domains([dom1, dom2])
    assert any(c.item_type == "domain" and c.severity == "WARNING" for c in fed_same.collisions)
    assert not any(c.item_type == "metric" and c.severity == "ERROR" for c in fed_same.collisions)

    rep_warn = SemanticLinter(steward_severity="WARNING").lint_ontology(fed_same)
    assert rep_warn.passed is True
    assert any(i.rule_id == "RULE_MISSING_STEWARD" and i.severity == "WARNING" for i in rep_warn.issues)

    # Different projects with whitespace-only formula differences do NOT collide; true diffs show both formulas
    dom_p1 = FederatedOntologyLoader.parse_yaml_string("""
domain: TeamA
source_project_id: proj-a
metrics:
  - name: Rev
    description: Revenue metric A
    data_steward: a@corp.com
    sql_expression: "SUM( x )"
""")
    dom_p2_same = FederatedOntologyLoader.parse_yaml_string("""
domain: TeamB
source_project_id: proj-b
metrics:
  - name: Rev
    description: Revenue metric B
    data_steward: b@corp.com
    sql_expression: "sum(x)"
""")
    fed_ws = FederatedOntologyLoader.merge_domains([dom_p1, dom_p2_same])
    assert not any(c.item_type == "metric" and c.severity == "ERROR" for c in fed_ws.collisions)

    dom_p2_diff = FederatedOntologyLoader.parse_yaml_string("""
domain: TeamC
source_project_id: proj-c
metrics:
  - name: Rev
    description: Revenue metric C
    data_steward: c@corp.com
    sql_expression: "SUM(y)"
""")
    fed_diff = FederatedOntologyLoader.merge_domains([dom_p1, dom_p2_diff])
    metric_errs = [c for c in fed_diff.collisions if c.item_type == "metric" and c.severity == "ERROR"]
    assert len(metric_errs) == 1
    assert "SUM( x )" in metric_errs[0].message and "SUM(y)" in metric_errs[0].message


