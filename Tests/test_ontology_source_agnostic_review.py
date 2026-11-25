"""TDD tests for source-agnostic pre-dry-run Ontology Review & Dry Run deduplication.

Covers:
1. Pre-dry-run extraction & duplicate detection for ALL source types:
   - fabric (live workspace models via FabricExtractor + TMSLToOSIConverter)
   - tableau (.tds / .twb / .tdsx / .twbx via TableauConnector)
   - snowflake (Semantic Views + tables)
   - databricks (Unity Catalog Metric Views + tables)
   - pbix (local .pbix files)
2. Applying Step 4 merged duplicates & conflict renames directly onto SML/OSI
   models (deduplicating redundant copies in-model and rewriting sibling refs)
   AND reflecting saved Step 4 ontology domain IDs in Step 5's Dry Run preview.
"""

from __future__ import annotations

import zipfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from semabridge.api.services import ontology_review_service as review_svc


SAMPLE_TMSL_A = {
    "model": {
        "name": "Sales Exec",
        "tables": [
            {
                "name": "Sales",
                "columns": [
                    {"name": "Amount", "dataType": "decimal", "sourceColumn": "Amount"},
                    {"name": "Cost", "dataType": "decimal", "sourceColumn": "Cost"},
                ],
                "measures": [
                    {"name": "Total Revenue", "expression": "SUM(Sales[Amount])"},
                    {"name": "Sales Amount", "expression": "=sum( 'Sales'[Amount] ) // duplicate"},
                    {"name": "Net Margin", "expression": "[Sales Amount] - SUM(Sales[Cost])"},
                ],
            }
        ],
    }
}

SAMPLE_TMSL_B = {
    "model": {
        "name": "Regional Ops",
        "tables": [
            {
                "name": "Sales",
                "columns": [
                    {"name": "Amount", "dataType": "decimal", "sourceColumn": "Amount"},
                ],
                "measures": [
                    {"name": "Gross Bookings", "expression": "SUM(Sales[Amount])"},
                ],
            }
        ],
    }
}


SAMPLE_TABLEAU_TDS = """<?xml version='1.0' encoding='utf-8' ?>
<datasource formatted-name='Orders Datasource' inline='true' version='18.1'>
  <connection class='federated'>
    <relation name='Orders' table='[dbo].[Orders]' type='table' />
    <metadata-records>
      <metadata-record class='column'>
        <remote-name>OrderID</remote-name>
        <local-name>[OrderID]</local-name>
        <parent-name>[Orders]</parent-name>
        <local-type>integer</local-type>
      </metadata-record>
      <metadata-record class='column'>
        <remote-name>Sales</remote-name>
        <local-name>[Sales]</local-name>
        <parent-name>[Orders]</parent-name>
        <local-type>real</local-type>
      </metadata-record>
    </metadata-records>
  </connection>
  <column caption='Total Sales' datatype='real' name='[Calculation_1]' role='measure' type='quantitative'>
    <calculation class='tableau' formula='SUM([Sales])' />
  </column>
  <column caption='Revenue Total' datatype='real' name='[Calculation_2]' role='measure' type='quantitative'>
    <calculation class='tableau' formula='sum( [Sales] )' />
  </column>
</datasource>
"""


# ---------------------------------------------------------------------------
# Slice 1: Source-agnostic pre-dry-run extraction & duplicate analysis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fabric_source_shows_deduplication_before_dry_run(monkeypatch):
    """Fabric workspace models are extracted to OSI and analyzed for duplicates before dry run."""
    defs = {
        "ds-exec": (SAMPLE_TMSL_A, "Sales Exec"),
        "ds-ops": (SAMPLE_TMSL_B, "Regional Ops"),
    }

    class FakeFabricExtractor:
        def __init__(self, cfg):
            self.cfg = cfg
            self._access_token = None
            self._token_expires_at = 0

        def resolve_model_id(self, name_or_id: str) -> str:
            mapping = {"Sales Exec": "ds-exec", "Regional Ops": "ds-ops", "Broken Model": "ds-broken"}
            return mapping.get(name_or_id, name_or_id)

        def get_model_definition(self, dataset_id: str):
            if dataset_id == "ds-broken":
                raise RuntimeError("Fabric REST timeout")
            return defs[dataset_id][0]

        def get_model_display_name(self, dataset_id: str) -> str:
            return defs[dataset_id][1]

    monkeypatch.setattr("semabridge.connectors.fabric_extractor.FabricExtractor", FakeFabricExtractor)
    # Live Fabric review reads with the project's own identity (sources.fabric.model_reader):
    # stub the identity's token and the workspace model listing.
    monkeypatch.setattr(
        "semabridge.sources.fabric.credentials.resolve_fabric_credentials",
        lambda **kw: SimpleNamespace(origin="identity", access_token="fake-token", expires_at=9e12),
    )
    monkeypatch.setattr(
        "semabridge.sources.fabric.validation.list_workspace_models",
        lambda ws, creds: [
            {"id": "ds-exec", "displayName": "Sales Exec"},
            {"id": "ds-ops", "displayName": "Regional Ops"},
            {"id": "ds-broken", "displayName": "Broken Model"},
        ],
    )

    out = await review_svc.run_analysis(
        source_config={"type": "fabric", "workspace_id": "11111111-1111-1111-1111-111111111111", "identity_id": "acc-fab"},
        selected_sources=["Sales Exec", "Regional Ops", "Broken Model"],
        project_id=None,
    )
    assert out["analysis_id"] is not None
    assert len(out["errors"]) == 1 and out["errors"][0]["source"] == "Broken Model"
    # Total Revenue, Sales Amount, and Gross Bookings all share SUM(Sales[Amount])
    assert out["summary"]["duplicate_groups"] >= 1
    dup_members = {m["name"] for d in out["duplicates"] for m in d["members"]}
    assert {"Total Revenue", "Sales Amount", "Gross Bookings"} <= dup_members


@pytest.mark.asyncio
async def test_tableau_source_shows_deduplication_before_dry_run(tmp_path):
    """Tableau .tds and .tdsx files are read into OSI and analyzed for duplicates before dry run."""
    tds_file = tmp_path / "orders.tds"
    tds_file.write_text(SAMPLE_TABLEAU_TDS, encoding="utf-8")

    tdsx_file = tmp_path / "orders_packaged.tdsx"
    with zipfile.ZipFile(tdsx_file, "w") as zf:
        zf.writestr("orders.tds", SAMPLE_TABLEAU_TDS)

    out = await review_svc.run_analysis(
        source_config={"type": "tableau", "path": str(tds_file)},
        selected_sources=[],
        project_id=None,
    )
    assert out["analysis_id"] is not None
    assert out["summary"]["duplicate_groups"] == 1
    members = {m["name"] for m in out["duplicates"][0]["members"]}
    assert members == {"Total Sales", "Revenue Total"}

    # Packaged .tdsx passed via selected_sources also works
    out_tdsx = await review_svc.run_analysis(
        source_config={"type": "tableau"},
        selected_sources=[str(tdsx_file)],
        project_id=None,
    )
    assert out_tdsx["analysis_id"] is not None
    assert out_tdsx["summary"]["duplicate_groups"] == 1


@pytest.mark.asyncio
async def test_snowflake_source_shows_deduplication_before_dry_run(monkeypatch):
    """Snowflake Semantic Views / tables are read into OSI before dry run."""
    from semabridge.intermediate.models import OSIDataset, OSIMetric, OSIModel

    fake_osi = OSIModel(
        unique_name="SF_SALES_SV",
        label="SF_SALES_SV",
        datasets=[OSIDataset(unique_name="SALES")],
        metrics=[
            OSIMetric(unique_name="REV_A", label="REV_A", dataset="SALES", sql_expression="SUM(SALES.AMOUNT)", expression="SUM(SALES.AMOUNT)"),
            OSIMetric(unique_name="REV_B", label="REV_B", dataset="SALES", sql_expression="sum( sales.amount )", expression="sum( sales.amount )"),
        ],
    )

    monkeypatch.setattr(
        review_svc,
        "_read_snowflake_models",
        lambda source_config, selected_sources: ([fake_osi], []),
        raising=False,
    )

    out = await review_svc.run_analysis(
        source_config={"type": "snowflake", "database": "ANALYTICS", "schema": "GOLD", "identity_id": "acc-sf"},
        selected_sources=["SF_SALES_SV"],
        project_id=None,
    )
    assert out["analysis_id"] is not None
    assert out["summary"]["duplicate_groups"] == 1


@pytest.mark.asyncio
async def test_databricks_source_metric_views_and_tables_show_deduplication_before_dry_run(monkeypatch):
    """Databricks Metric Views and tables are parsed into OSI and analyzed for duplicates before dry run."""
    metric_view_yaml_1 = """version: 0.1
source: main.gold.fact_orders
dimensions:
  - name: region
    expr: region
measures:
  - name: total_revenue
    expr: SUM(amount)
    comment: Gross order revenue
  - name: order_revenue_dup
    expr: sum( amount )
"""
    calls = []

    def fake_dbx_query(creds, statement):
        calls.append(statement)
        if statement.upper().startswith("SHOW TABLES"):
            return [
                {"tableName": "mv_orders", "database": "gold"},
                {"tableName": "fact_orders", "database": "gold"},
            ]
        if "mv_orders" in statement:
            return [
                {"col_name": "region", "data_type": "string", "comment": ""},
                {"col_name": "total_revenue", "data_type": "decimal(18,2)", "comment": ""},
                {"col_name": "# Detailed Table Information", "data_type": "", "comment": ""},
                {"col_name": "Type", "data_type": "METRIC_VIEW", "comment": ""},
                {"col_name": "View Text", "data_type": metric_view_yaml_1, "comment": ""},
            ]
        if "fact_orders" in statement:
            return [
                {"col_name": "order_id", "data_type": "bigint", "comment": ""},
                {"col_name": "amount", "data_type": "decimal(18,2)", "comment": "Order amount"},
            ]
        return []

    monkeypatch.setattr(
        review_svc,
        "_resolve_databricks_source_creds",
        lambda source_config: {"host": "dbc.cloud.databricks.com", "warehouse_id": "wh-1", "token": "dapi-test"},
        raising=False,
    )
    monkeypatch.setattr(review_svc, "_exec_databricks_rows", fake_dbx_query, raising=False)

    out = await review_svc.run_analysis(
        source_config={"type": "databricks", "catalog": "main", "schema": "gold", "identity_id": "acc-dbx"},
        selected_sources=["main.gold.mv_orders", "main.gold.fact_orders"],
        project_id=None,
    )
    assert out["analysis_id"] is not None
    assert out["errors"] == []
    assert out["summary"]["duplicate_groups"] >= 1
    dup_names = {m["name"] for d in out["duplicates"] for m in d["members"]}
    assert {"total_revenue", "order_revenue_dup"} <= dup_names


def test_review_analyze_api_supports_fabric_tableau_snowflake_databricks(tmp_path, monkeypatch):
    """POST /api/ontology/review/analyze succeeds for non-PBIX sources instead of returning supported=False."""
    from semabridge.api.main import app
    from semabridge.ontology.store import ontology_store

    tds_file = tmp_path / "sales.tds"
    tds_file.write_text(SAMPLE_TABLEAU_TDS, encoding="utf-8")

    orig_domains, orig_dir = ontology_store.domains, ontology_store.storage_dir
    ontology_store.domains, ontology_store.storage_dir = {}, tmp_path / "onto"
    try:
        client = TestClient(app)
        res = client.post(
            "/api/ontology/review/analyze",
            json={"source_config": {"type": "tableau", "path": str(tds_file)}, "selected_sources": []},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["supported"] is True
        assert body["summary"]["duplicate_groups"] == 1
    finally:
        ontology_store.domains, ontology_store.storage_dir = orig_domains, orig_dir
        ontology_store._refresh_index()


# ---------------------------------------------------------------------------
# Slice 2: Step 4 deduplication merges & renames applied to SML / Dry Run
# ---------------------------------------------------------------------------


def test_intra_model_merge_deduplicates_sml_and_osi_metrics_and_rewrites_sibling_refs():
    """Merging duplicate measures in Step 4 collapses duplicate copies in SML/OSI into one canonical metric."""
    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
    from semabridge.ontology.apply import apply_ontology_to_model
    from semabridge.ontology.federated_loader import FederatedOntologyLoader
    from semabridge.ontology.quality import apply_review_decisions

    osi = TMSLToOSIConverter().to_osi(
        {"tmsl": SAMPLE_TMSL_A, "workspace_id": "ws", "dataset_id": "ds-exec", "display_name": "Sales Exec"}
    )
    sml = OSIToSMLConverter(target_type="databricks").from_osi(osi)
    assert len(sml.metrics) == 3  # Total Revenue, Sales Amount, Net Margin

    draft = FederatedOntologyLoader.from_osi_model(model=osi, domain_name="Sales")
    reviewed_dom, _ = apply_review_decisions(
        draft,
        merges=[{"name": "revenue", "members": ["Total Revenue", "Sales Amount"]}],
        descriptions={"Total Revenue": "Canonical gross revenue"},
    )

    report = apply_ontology_to_model(sml, domains=[reviewed_dom])
    apply_ontology_to_model(osi, domains=[reviewed_dom])

    sml_names = [m.unique_name for m in sml.metrics]
    assert "revenue" in sml_names
    assert "Total Revenue" not in sml_names
    assert "Sales Amount" not in sml_names
    assert len(sml.metrics) == 2  # revenue + Net Margin

    rev = next(m for m in sml.metrics if m.unique_name == "revenue")
    assert "Canonical gross revenue" in (rev.description or "")
    assert {"Total Revenue", "Sales Amount"} <= set(rev.synonyms)

    margin = next(m for m in osi.metrics if m.unique_name == "Net Margin")
    assert "[revenue]" in (margin.expression or "")
    assert report["changes"].get("merged_metrics")


def test_cross_model_merge_and_conflict_rename_update_metric_names():
    """Cross-model merges unify names per model domain, and keep_new conflict resolution renames metrics."""
    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
    from semabridge.ontology.apply import apply_ontology_to_model

    osi_b = TMSLToOSIConverter().to_osi(
        {"tmsl": SAMPLE_TMSL_B, "workspace_id": "ws", "dataset_id": "ds-ops", "display_name": "Regional Ops"}
    )
    osi_a = TMSLToOSIConverter().to_osi(
        {"tmsl": SAMPLE_TMSL_A, "workspace_id": "ws", "dataset_id": "ds-exec", "display_name": "Sales Exec"}
    )

    built = review_svc.build_reviewed_domains(
        [osi_a, osi_b],
        domain_name="Enterprise Sales",
        owner="Data Team",
        merges=[{"name": "revenue", "members": ["Total Revenue", "Sales Amount", "Gross Bookings"]}],
        conflicts=[{"name": "Net Margin", "resolution": "keep_new", "new_name": "net_margin_exec"}],
        descriptions={"revenue": "Unified enterprise revenue"},
    )
    dom_by_name = {mname: dom for dom, _rep, _mid, mname in built}

    sml_b = OSIToSMLConverter(target_type="databricks").from_osi(osi_b)
    apply_ontology_to_model(sml_b, domains=[dom_by_name["Regional Ops"]])
    assert [m.unique_name for m in sml_b.metrics] == ["revenue"]
    assert "Gross Bookings" in sml_b.metrics[0].synonyms

    sml_a = OSIToSMLConverter(target_type="databricks").from_osi(osi_a)
    apply_ontology_to_model(sml_a, domains=[dom_by_name["Sales Exec"]])
    assert {m.unique_name for m in sml_a.metrics} == {"revenue", "net_margin_exec"}


def test_explicit_ontology_domain_ids_apply_in_databricks_dry_run_preview(tmp_path):
    """Saved Step 4 ontology domain IDs deduplicate measures in Step 5's Databricks Dry Run plan before project creation."""
    from types import SimpleNamespace
    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
    from semabridge.core.behavior import ConnectorBehavior
    from semabridge.core.engine.deployment.databricks_preview import preview_databricks_plan
    from semabridge.ontology.apply import apply_ontology_for_run
    from semabridge.ontology.store import DomainOntologyStore

    store = DomainOntologyStore(storage_dir=tmp_path / "ontologies")
    osi_a = TMSLToOSIConverter().to_osi(
        {"tmsl": SAMPLE_TMSL_A, "workspace_id": "ws", "dataset_id": "ds-exec", "display_name": "Sales Exec"}
    )
    osi_b = TMSLToOSIConverter().to_osi(
        {"tmsl": SAMPLE_TMSL_B, "workspace_id": "ws", "dataset_id": "ds-ops", "display_name": "Regional Ops"}
    )
    built = review_svc.build_reviewed_domains(
        [osi_a, osi_b],
        domain_name="Sales",
        owner="RevOps",
        merges=[{"name": "revenue", "members": ["Total Revenue", "Sales Amount", "Gross Bookings"]}],
        conflicts=[],
        descriptions={"revenue": "Deduplicated revenue"},
    )
    saved_ids = []
    for dom, _rep, _mid, _mname in built:
        store.add_or_update_domain(dom, save_to_disk=True)
        saved_ids.append(dom.id)

    sml_a = OSIToSMLConverter(target_type="databricks").from_osi(osi_a)
    ctx = SimpleNamespace(
        project_id="preview-abc123",
        sml_model=sml_a,
        osi_model=osi_a,
        pbix_path=None,
        resolved_pbix_display_name="Sales Exec",
        semantic_view_name_override=None,
        config_payload={"ontology_domain_ids": saved_ids},
        config=SimpleNamespace(
            targets=[SimpleNamespace(type="databricks", catalog="workspace", schema="default", warehouse_id="")],
            target=None,
            _databricks=None,
        ),
        behavior=ConnectorBehavior(),
        ontology_report=None,
    )
    fake_engine = SimpleNamespace(_summary=None)

    from semabridge.ontology import store as store_mod
    orig_store = store_mod.ontology_store
    store_mod.ontology_store = store
    try:
        apply_ontology_for_run(fake_engine, ctx)
    finally:
        store_mod.ontology_store = orig_store

    plan = preview_databricks_plan(ctx, lookup=False)
    assert plan["views"], plan
    measure_names = [m["name"] for m in plan["views"][0]["measures"]]
    assert "revenue" in measure_names
    assert "total_revenue" not in measure_names
    assert "sales_amount" not in measure_names
