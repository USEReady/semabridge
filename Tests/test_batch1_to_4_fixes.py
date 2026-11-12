"""Focused regression tests for Batch 1 (Ontology/Upsert/Governance), Batch 2 (RLS),
and Batch 4 (Binding, Structure, Hidden/SummarizeBy/FACTS, DAX/SQL Cleanup).
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from semabridge.api.main import app
from semabridge.auth.tokens import create_access_token
from semabridge.connectors.alias_registry import AliasRegistry
from semabridge.connectors.dimensions_clause_builder import DimensionsClauseBuilder
from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
from semabridge.connectors.semantic_ddl_sanitizer import SemanticDDLSanitizer
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.connectors.snowflake_metric_sql import simplify_dead_arithmetic_sql
from semabridge.connectors.tables_clause_builder import TablesClauseBuilder
from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter, tmsl_relationship_cardinality
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.reconciliation import ReconciliationReport
from semabridge.core.settings import SnowflakeConfig
from semabridge.core.upsert_delta import compute_delta
from semabridge.core.validation.primary_key_resolver import PrimaryKeyResolver
from semabridge.intermediate.models import (
    OSIAggregationType,
    OSICardinality,
    OSIColumn,
    OSICrossFilterDirection,
    OSIDataType,
    OSIDataset,
    OSIDimension,
    OSIHierarchy,
    OSILevel,
    OSIMetric,
    OSIModel,
    OSIRelationship,
)
from semabridge.ontology.federated_loader import FederatedOntologyLoader
from semabridge.ontology.models import DomainOntology
from semabridge.utils.identifiers import IdentifierSanitizer

os.environ.setdefault("JWT_SECRET_KEY", "pytest-only-secret")
_ADMIN_HEADERS = {"Authorization": "Bearer " + create_access_token({"sub": "1", "role": "admin"})}


def _sf_cfg():
    return SnowflakeConfig(
        account="test.local",
        user="u",
        password="p",
        warehouse="w",
        database="TEST_DB",
        schema_name="PUBLIC",
        role="r",
    )


# ---------------------------------------------------------------------------
# Batch 1: Ontology Fidelity, Immutability, Conflict Detection, Upsert Delta
# ---------------------------------------------------------------------------


def test_ontology_preserves_hidden_format_crossfilter_hierarchies_and_formulas():
    osi = OSIModel(
        unique_name="SalesModel",
        label="SalesModel",
        datasets=[
            OSIDataset(
                unique_name="Orders",
                source_table="ORDERS",
                is_fact=True,
                columns=[
                    OSIColumn(unique_name="OrderID", data_type=OSIDataType.STRING, is_key=True),
                    OSIColumn(
                        unique_name="InternalCode",
                        data_type=OSIDataType.STRING,
                        is_hidden=True,
                        format_string="0000",
                    ),
                ],
            ),
            OSIDataset(
                unique_name="Geography",
                source_table="GEOGRAPHY",
                is_fact=False,
                columns=[OSIColumn(unique_name="Region", data_type=OSIDataType.STRING, is_key=True)],
            ),
        ],
        dimensions=[
            OSIDimension(
                unique_name="Geography",
                dataset="Geography",
                hierarchies=[
                    OSIHierarchy(
                        unique_name="GeoHierarchy",
                        levels=[OSILevel(unique_name="RegionLevel", attribute="Region")],
                    )
                ],
            )
        ],
        relationships=[
            OSIRelationship(
                unique_name="REL_ORDERS_ORDERID__GEOGRAPHY_REGION",
                from_dataset="Orders",
                from_columns=["OrderID"],
                to_dataset="Geography",
                to_columns=["Region"],
                cardinality=OSICardinality.MANY_TO_ONE,
                cross_filter_direction=OSICrossFilterDirection.BOTH,
            )
        ],
        metrics=[
            OSIMetric(
                unique_name="Revenue",
                dataset="Orders",
                expression="SUM(Orders[Amount])",
                sql_expression='SUM(ORDERS."AMOUNT")',
                is_hidden=True,
                format_string="$#,##0",
            )
        ],
    )

    dom = FederatedOntologyLoader.from_osi_model(osi, domain_name="SalesDomain")
    orders_concept = next(c for c in dom.concepts if c.name == "Orders")
    hidden_attr = next(a for a in orders_concept.attributes if a.name == "InternalCode")
    assert hidden_attr.is_hidden is True
    assert hidden_attr.format_string == "0000"

    geo_concept = next(c for c in dom.concepts if c.name == "Geography")
    assert len(geo_concept.hierarchies) == 1
    assert geo_concept.hierarchies[0].name == "GeoHierarchy"
    assert orders_concept.name == "Orders"
    assert dom.relationships[0].cross_filter_direction == "both"

    rev_metric = next(m for m in dom.metrics if m.name == "Revenue")
    assert rev_metric.dax_expression == "SUM(Orders[Amount])"
    assert rev_metric.sql_expression == 'SUM(ORDERS."AMOUNT")'
    assert rev_metric.is_hidden is True
    assert rev_metric.format_string == "$#,##0"

    rebuilt = FederatedOntologyLoader.to_osi_model(FederatedOntologyLoader.merge_domains([dom]))
    rebuilt_metric = next(m for m in rebuilt.metrics if m.unique_name == "Revenue")
    assert rebuilt_metric.expression == "SUM(Orders[Amount])"
    assert rebuilt_metric.sql_expression == 'SUM(ORDERS."AMOUNT")'
    assert rebuilt_metric.is_hidden is True
    assert rebuilt_metric.format_string == "$#,##0"


def test_cross_domain_metric_collision_warning_vs_blocker():
    d1 = DomainOntology.model_validate({
        "domain": "DeptA",
        "metrics": [{"name": "Gross Margin", "sql_expression": "SUM(a) - SUM(b)"}],
    })
    d2_same = DomainOntology.model_validate({
        "domain": "DeptB",
        "metrics": [{"name": "gross_margin", "sql_expression": " sum(a) - sum(b) "}],
    })
    d2_diff = DomainOntology.model_validate({
        "domain": "DeptC",
        "metrics": [{"name": "gross_margin", "sql_expression": "SUM(a) * 0.5"}],
    })

    fed_same = FederatedOntologyLoader.merge_domains([d1, d2_same])
    assert len(fed_same.collisions) == 1
    assert fed_same.collisions[0].severity == "WARNING"

    fed_diff = FederatedOntologyLoader.merge_domains([d1, d2_diff])
    assert len(fed_diff.collisions) == 1
    assert fed_diff.collisions[0].severity == "ERROR"


def test_pbix_formula_readonly_and_metadata_patch_endpoint(tmp_path, monkeypatch):
    from semabridge.ontology.store import DomainOntologyStore
    import semabridge.api.routers.domain_ontology_router as dor

    store = DomainOntologyStore(storage_dir=tmp_path)
    monkeypatch.setattr(dor, "ontology_store", store)

    client = TestClient(app, headers=_ADMIN_HEADERS)
    dom = DomainOntology.model_validate({
        "domain": "PBIXDomain",
        "source_project_id": "proj-pbix",
        "metrics": [
            {
                "name": "NetSales",
                "origin": "bi",
                "dax_expression": "SUM(Sales[Net])",
                "sql_expression": 'SUM(SALES."NET")',
                "description": "Initial",
                "synonyms": ["Net"],
            }
        ],
    })
    store.add_or_update_domain(dom)

    # Attempt to overwrite formula via upload -> 422
    bad_yaml = """
domain: PBIXDomain
source_project_id: proj-pbix
metrics:
  - name: NetSales
    dax_expression: "SUM(Sales[Gross])"
    sql_expression: 'SUM(SALES."GROSS")'
"""
    resp = client.post(
        "/api/ontology/domains/upload",
        files=[("files", ("pbix.yaml", bad_yaml.encode("utf-8"), "application/x-yaml"))],
    )
    assert resp.status_code == 422
    assert "read-only" in resp.text.lower()

    # PATCH metadata succeeds cleanly
    patch_resp = client.patch(
        "/api/ontology/domains/PBIXDomain/metrics/NetSales/metadata",
        json={"description": "Updated desc", "synonyms": ["Net Sales", "Revenue Net"]},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["metric"]["description"] == "Updated desc"
    assert patch_resp.json()["metric"]["synonyms"] == ["Net Sales", "Revenue Net"]


def test_upsert_delta_detects_metadata_changed_for_description_and_synonyms():
    baseline = {
        "datasets": [],
        "relationships": [],
        "metrics": [
            {
                "unique_name": "TotalSales",
                "dataset": "Sales",
                "aggregation": "sum",
                "source_column": "Amount",
                "description": "Old desc",
                "synonyms": ["Sales"],
            }
        ],
    }
    current = SimpleNamespace(
        datasets=[],
        relationships=[],
        metrics=[
            SimpleNamespace(
                unique_name="TotalSales",
                dataset="Sales",
                aggregation="sum",
                source_column="Amount",
                expression="",
                sql_expression="",
                description="New desc",
                synonyms=["Sales", "Turnover"],
            )
        ],
    )
    delta = compute_delta(current, baseline)
    assert delta["has_changes"] is True
    assert delta["metadata_changed"] is True
    assert any(m["name"] == "TotalSales" and m.get("metadata_changed") for m in delta["kinds"]["metrics"]["modified"])


# ---------------------------------------------------------------------------
# Batch 2: RLS omits default database -> HTTP 400 + extra fields forbidden -> 422
# ---------------------------------------------------------------------------


def test_rls_requires_explicit_target_database_and_forbids_extra_fields():
    client = TestClient(app, headers=_ADMIN_HEADERS)

    resp_missing_db = client.post(
        "/api/security/rls/tmdl-to-snowflake",
        json={"roles": [{"name": "RegionRole", "table_permissions": []}], "target_schema": "SEC"},
    )
    assert resp_missing_db.status_code == 400

    resp_extra = client.post(
        "/api/security/rls/tmdl-to-snowflake",
        json={"roles": [], "target_database": "ANALYTICS", "unexpected_field": 123},
    )
    assert resp_extra.status_code == 422


# ---------------------------------------------------------------------------
# Batch 4: Reconciliation, Structure, Hidden/SummarizeBy/FACTS, SQL Cleanup
# ---------------------------------------------------------------------------


def test_reconciliation_preserves_year_suffixed_metric_names():
    report = ReconciliationReport(
        run_id="r1",
        project_id="p1",
        snapshot_id="s1",
        snapshot_metric_names=["Sales_2025", "Total Revenue"],
        deployed_live_metrics={"SALES_2025", "TOTAL_REVENUE"},
        deployed_dead_metrics=set(),
        drop_records=[],
    )
    assert report.is_clean() is True


def test_tables_builder_keeps_1col_named_id_and_uses_strict_n1_fact_and_no_col0_pk():
    id_san = IdentifierSanitizer()
    mock_sm = MagicMock()
    mock_sm.declared_primary_keys.return_value = {}
    mock_sm._resolve_physical_column_name.side_effect = lambda ds, c, model=None: id_san.sanitize_column(c)

    # Disconnected 1-column table named "ID" must NOT be dropped as measure-only
    lookup_ds = SimpleNamespace(
        unique_name="LookupIds",
        source_table="LOOKUP_IDS",
        is_fact=False,
        is_measure_table=False,
        columns=[SimpleNamespace(unique_name="ID", is_key=False)],
    )
    # Two tables linked ONLY by 1:1 -> neither is N:1 fact even if named FactTable
    fact_like = SimpleNamespace(
        unique_name="FactLike",
        source_table="FACT_LIKE",
        is_fact=False,
        is_measure_table=False,
        columns=[SimpleNamespace(unique_name="PK", is_key=True), SimpleNamespace(unique_name="AMT", is_key=False)],
    )
    other_1to1 = SimpleNamespace(
        unique_name="OtherSide",
        source_table="OTHER_SIDE",
        is_fact=False,
        is_measure_table=False,
        columns=[SimpleNamespace(unique_name="PK", is_key=True)],
    )
    rel_1to1 = SimpleNamespace(
        from_dataset="FactLike",
        to_dataset="OtherSide",
        from_columns=["PK"],
        to_columns=["PK"],
        cardinality="one_to_one",
        is_active=True,
    )

    builder = TablesClauseBuilder(
        id_san,
        mock_sm,
        _sf_cfg(),
        SimpleNamespace(snowflake=SimpleNamespace(pk_resolution_mode=None, auto_add_anchors=False, source_table_mapping={})),
        {"LOOKUP_IDS": {"ID"}, "FACT_LIKE": {"PK", "AMT"}, "OTHER_SIDE": {"PK"}},
    )
    lines, _, _, _, _, _ = builder.build_for_osi(
        SimpleNamespace(datasets=[lookup_ds, fact_like, other_1to1], relationships=[rel_1to1], metrics=[]),
        registry=AliasRegistry(),
        metric_counts_by_dataset={"FactLike": 1},
        related_datasets={"FactLike", "OtherSide"},
    )
    assert any("LOOKUP_IDS" in l for l in lines)
    lookup_line = [l for l in lines if "LOOKUP_IDS" in l][0]
    assert "PRIMARY KEY" not in lookup_line  # no columns[0] fake PK fallback

    pk_res = PrimaryKeyResolver(id_san, pk_mode="permissive").resolve_single(lookup_ds, [])
    assert pk_res.pk_columns == []


def test_numeric_cardinality_and_implicit_metrics_and_facts_and_hidden_private():
    assert tmsl_relationship_cardinality({"fromCardinality": 2, "toCardinality": 1}) == OSICardinality.MANY_TO_ONE
    assert tmsl_relationship_cardinality({"fromCardinality": 1, "toCardinality": 1}) == OSICardinality.ONE_TO_ONE

    tmsl_payload = {
        "model": {
            "tables": [
                {
                    "name": "Orders",
                    "columns": [
                        {"name": "CustomerID", "dataType": "int64", "isKey": True, "summarizeBy": "sum"},
                        {"name": "Amount", "dataType": "decimal", "summarizeBy": "sum"},
                        {"name": "SecretMargin", "dataType": "double", "summarizeBy": "avg", "isHidden": True},
                        {"name": "DefaultCol", "dataType": "int64", "summarizeBy": "default"},
                    ],
                },
                {
                    "name": "Returns",
                    "columns": [
                        {"name": "CustomerID", "dataType": "int64", "summarizeBy": "none"},
                        {"name": "Amount", "dataType": "decimal", "summarizeBy": "sum"},
                    ],
                },
                {
                    "name": "Customers",
                    "columns": [
                        {"name": "CustomerID", "dataType": "int64", "isKey": True},
                        {"name": "Segment", "dataType": "string"},
                        {"name": "InternalNote", "dataType": "string", "isHidden": True},
                    ],
                },
            ],
            "relationships": [
                {
                    "fromTable": "Orders",
                    "fromColumn": "CustomerID",
                    "toTable": "Customers",
                    "toColumn": "CustomerID",
                    "fromCardinality": 2,
                    "toCardinality": 1,
                },
                {
                    "fromTable": "Returns",
                    "fromColumn": "CustomerID",
                    "toTable": "Customers",
                    "toColumn": "CustomerID",
                    "fromCardinality": 2,
                    "toCardinality": 1,
                },
            ],
        }
    }
    osi = TMSLToOSIConverter().to_osi({"tmsl": tmsl_payload, "dataset_id": "TestModel"})
    metric_names = {m.unique_name for m in osi.metrics}
    # Colliding SUM_OF_AMOUNT across Orders & Returns gets table-qualified; hidden SecretMargin & key excluded.
    # Unset ("default") summarization on a numeric fact column sums, as in Power BI.
    assert metric_names == {"SUM_OF_ORDERS_AMOUNT", "SUM_OF_RETURNS_AMOUNT", "SUM_OF_DEFAULTCOL"}

    # Generate Snowflake DDL and check FACTS + PRIVATE hidden dimension
    ddl = SnowflakeEmitter(_sf_cfg(), ConnectorBehavior()).generate_ddls_from_osi(osi)[0]
    assert "FACTS (" in ddl
    assert 'ORDERS."AMOUNT" AS ORDERS."AMOUNT"' in ddl
    assert 'PRIVATE ORDERS."SECRETMARGIN" AS ORDERS."SECRETMARGIN"' in ddl
    assert 'PRIVATE CUSTOMERS."INTERNALNOTE" AS CUSTOMERS."INTERNALNOTE"' in ddl


def test_dead_arithmetic_and_non_additive_preservation():
    assert simplify_dead_arithmetic_sql('SUM(A."B") * 1 + 0 - 0.0 / 1') == 'SUM(A."B")'
    # Must NOT simplify * 0 or 0 - x or break precedence of + 0 * Y
    assert simplify_dead_arithmetic_sql('SUM(A."B") * 0') == 'SUM(A."B") * 0'
    assert simplify_dead_arithmetic_sql('0 - SUM(A."B")') == '0 - SUM(A."B")'
    assert simplify_dead_arithmetic_sql('A + 0 * B') == 'A + 0 * B'

    id_san = IdentifierSanitizer()
    builder = MetricsClauseBuilder(
        id_san,
        SimpleNamespace(_resolve_physical_column_name=lambda ds, c, model=None: id_san.sanitize_column(c)),
        SemanticDDLSanitizer(id_san),
        translator=None,
        config=None,
    )
    lines = [
        '  BAL."ENDING_BAL" NON ADDITIVE BY ( CAL."DATE" ASC NULLS LAST ) AS SUM(BAL."AMT")',
        '  "DOUBLE_BAL" AS "ENDING_BAL" + 1',
    ]
    pruned = builder._prune_unresolved_metric_lines(lines, {"ENDING_BAL", "DOUBLE_BAL"})
    double_line = [l for l in pruned if '"DOUBLE_BAL"' in l][0]
    # Keeps metric-by-name reference instead of inlining SUM(BAL."AMT")
    assert '"ENDING_BAL" + 1' in double_line
    assert 'SUM(BAL."AMT")' not in double_line


def test_persisted_summary_json_restores_portfolio_metrics_without_manifest(tmp_path, monkeypatch):
    import asyncio
    import json
    import semabridge.api.services.run_service as run_svc
    import semabridge.api.services.run_report_service as rr_svc
    import semabridge.api.services.run_model_results as rmr_svc

    monkeypatch.setattr(rr_svc, "REPORTS_ROOT", tmp_path)
    proj_dir = tmp_path / "proj-restored"
    proj_dir.mkdir(parents=True)
    summary_file = proj_dir / "run-123__ModelA.summary.json"
    summary_file.write_text(
        json.dumps({
            "run_id": "run-123",
            "project_name": "Restored",
            "status": "success",
            "crashed": False,
            "source_files": ["ModelA.pbix"],
            "counts": {
                "tables": 4,
                "columns": 12,
                "calculations": 5,
                "relationships": 3,
                "standard": 4,
                "ai_assisted": 1,
                "needs_review": 0,
                "dropped": 0,
                "excluded_by_design": 0,
            },
            "sections": {"standard": [{"name": "M1"}], "ai_assisted": [{"name": "M2", "confidence": 0.9}], "needs_review": []},
        }),
        encoding="utf-8",
    )

    # Simulate post-restart run record with stripped results and no manifest.json
    monkeypatch.setattr(run_svc, "_compat_ensure_loaded", lambda: None)
    monkeypatch.setattr(run_svc, "_compat_project_runs", {
        "proj-restored": [{"run_id": "run-123", "project_id": "proj-restored", "status": "success", "results": []}]
    })
    monkeypatch.setattr(run_svc, "_project_cfg_for_report", lambda pid: {})
    monkeypatch.setattr(rmr_svc, "load_run_model_results", lambda pid, rid: [])

    data = asyncio.run(run_svc.get_run_report_data_compat("proj-restored", "run-123"))
    assert data is not None
    assert data["crashed"] is False
    assert data["counts"]["tables"] == 4
    assert data["counts"]["ai_assisted"] == 1
    assert data["source_files"] == ["ModelA.pbix"]


def test_table_binding_excludes_calculated_and_rownumber_columns():
    from Tests.test_table_binding_determinism import _Conn, _Cursor, _schema_manager
    from semabridge.connectors.schema_manager import clear_schema_cache
    from semabridge.sml.models import SMLColumn, SMLDataset, SMLModel

    clear_schema_cache()
    conn = _Conn(
        tables=["MANUFACTURER"],
        columns={"MANUFACTURER": ["MANUFACTURERID", "MANUFACTURER"]},
        row_counts={"MANUFACTURER": 10},
    )
    manager = _schema_manager(conn)
    model = SMLModel(
        unique_name="CalcColModel",
        datasets=[
            SMLDataset(
                unique_name="Manufacturer",
                columns=[
                    SMLColumn(unique_name="ManufacturerID"),
                    SMLColumn(unique_name="Manufacturer"),
                    SMLColumn(unique_name="Logo", is_calculated=True, source_expression="SWITCH([ManufacturerID], 1, 'A')"),
                    SMLColumn(unique_name="RowNumber-123"),
                ],
            )
        ],
    )
    plan = {e["dataset"]: e for e in manager.plan_source_tables(_Cursor(), model)}
    assert plan["Manufacturer"]["action"] == "bind"
    assert plan["Manufacturer"]["table"] == "MANUFACTURER"
    clear_schema_cache()
