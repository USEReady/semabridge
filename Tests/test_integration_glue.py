"""Integration glue between the ingestion, DAX, emitter and ontology fixes.

Covers the hand-offs each component left for the others:
- translation advisories reach the deployed metric COMMENT and Cortex YAML;
- calculated tables / field parameters are not emitted as physical tables;
- DAX calculated columns are not emitted as physical dimensions and are
  ledgered as by-design;
- inferred relationships are the first to be dropped in a path conflict;
- table descriptions / synonyms reach the Cortex YAML;
- "sync with OSI" resolves the domain's source project.
"""

from __future__ import annotations

import pytest
import yaml
from fastapi.testclient import TestClient

from semabridge.api.main import app
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.connectors.snowflake_emitter_parts.semantic_view_items import (
    select_conflicting_relationships,
)
from semabridge.connectors.synonym_clause import metric_comment_text
from semabridge.converter.dax_ast_parser import (
    ADVISORY_CATEGORY_TI_ANCHORED_TO_CURRENT_DATE,
    ADVISORY_NOTE_TI_ANCHORED_TO_CURRENT_DATE,
)
from semabridge.converter.osi_to_sml import _TRANSLATION_ADVISORY_NOTES
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig
from semabridge.ontology.models import DomainOntology
from semabridge.ontology.store import DomainOntologyStore
from semabridge.sml.models import (
    AggregationType,
    DataType,
    SMLColumn,
    SMLDataset,
    SMLMetric,
    SMLModel,
)

client = TestClient(app)

NOTE = "Values differ when the data does not extend to today."


def _emitter() -> SnowflakeEmitter:
    cfg = SnowflakeConfig(
        account="a", user="u", password="p", warehouse="w", database="DB", schema_name="SC", role="r"
    )
    return SnowflakeEmitter(cfg, ConnectorBehavior())


def _sml() -> SMLModel:
    return SMLModel(
        unique_name="Retail",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                source_table="Sales",
                is_fact=True,
                description="Sales fact table",
                synonyms=["Orders"],
                columns=[
                    SMLColumn(unique_name="Amount", data_type=DataType.DECIMAL),
                    SMLColumn(unique_name="Region", data_type=DataType.STRING),
                    SMLColumn(
                        unique_name="Margin Band",
                        data_type=DataType.STRING,
                        is_calculated=True,
                        expression='IF(Sales[Amount] > 100, "High", "Low")',
                        source_expression='IF(Sales[Amount] > 100, "High", "Low")',
                    ),
                ],
            ),
            SMLDataset(
                unique_name="Calendar Helper",
                source_table="Calendar Helper",
                dataset_kind="calculated_table",
                expression="CALENDAR(DATE(2020,1,1), DATE(2025,12,31))",
                columns=[SMLColumn(unique_name="Date", data_type=DataType.DATE)],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="Total Sales",
                dataset="Sales",
                sql_expression='SUM(SALES."AMOUNT")',
                aggregation=AggregationType.SUM,
                description="Revenue",
                advisory_notes=[NOTE],
                advisory_categories=[ADVISORY_CATEGORY_TI_ANCHORED_TO_CURRENT_DATE],
            )
        ],
    )


def test_advisory_note_registered_for_current_date_anchor():
    assert (
        _TRANSLATION_ADVISORY_NOTES[ADVISORY_CATEGORY_TI_ANCHORED_TO_CURRENT_DATE]
        == ADVISORY_NOTE_TI_ANCHORED_TO_CURRENT_DATE
    )


def test_metric_comment_text_combines_description_and_advisories():
    sml = _sml()
    assert metric_comment_text(sml.metrics[0]) == f"Revenue Note: {NOTE}"
    sml.metrics[0].advisory_notes = []
    assert metric_comment_text(sml.metrics[0]) == "Revenue"


def test_ddl_carries_advisory_skips_calc_table_and_calc_column():
    emitter = _emitter()
    ddl = "\n".join(emitter.generate_ddls(_sml()))

    metric_line = next(l for l in ddl.splitlines() if "TOTAL_SALES" in l.upper() and " AS " in l)
    assert "Note: " + NOTE in metric_line, metric_line

    # Calculated table without a materialized Snowflake object is not a TABLES entry.
    assert "CALENDAR_HELPER" not in ddl.upper(), ddl
    # Calculated column is not emitted as a physical dimension ...
    assert "MARGIN_BAND" not in ddl.upper(), ddl
    # ... and is ledgered as by-design with its DAX.
    recs = [
        r for r in emitter.drop_ledger.records
        if r.entity_kind == "column" and r.entity_name == "Margin Band"
    ]
    assert recs, [r.entity_name for r in emitter.drop_ledger.records]
    assert recs[0].by_design is True
    assert recs[0].reason.startswith("calculated column")
    assert "IF(Sales[Amount]" in (recs[0].detail or "")


def test_cortex_yaml_has_table_description_synonyms_and_advisory():
    out = yaml.safe_load(_emitter().generate_cortex_yaml(_sml()))
    tables = {t["name"]: t for t in out["semantic_model"]["tables"]}
    assert "Calendar Helper" not in tables
    sales = tables["Sales"]
    assert sales["description"] == "Sales fact table"
    assert sales["synonyms"] == ["Orders"]
    assert sales["measures"][0]["description"] == f"Revenue Note: {NOTE}"


def test_inferred_relationship_dropped_before_real_one():
    # Two paths A->B: the inferred one (priority 2) must be the one dropped.
    drop = select_conflicting_relationships([("A", "B"), ("A", "B")], [2, 0])
    assert 0 in drop and 1 not in drop


@pytest.fixture
def store(tmp_path, monkeypatch):
    from semabridge.ontology import store as store_mod
    import semabridge.api.routers.domain_ontology_router as router_mod

    s = DomainOntologyStore(storage_dir=tmp_path / "ontologies")
    monkeypatch.setattr(store_mod, "ontology_store", s)
    monkeypatch.setattr(router_mod, "ontology_store", s)
    return s


def test_sync_without_source_project_is_a_clear_400(store):
    store.add_or_update_domain(DomainOntology(domain="Finance"), save_to_disk=False)
    res = client.post("/api/ontology/domains/from-osi", json={"domain_name": "Finance"})
    assert res.status_code == 400
    assert "no source project" in res.text


def test_sync_uses_domain_source_project(store, monkeypatch):
    store.add_or_update_domain(
        DomainOntology(domain="Finance", source_project_id="proj-42"), save_to_disk=False
    )
    seen = {}

    async def fake_load(project_id):
        seen["project_id"] = project_id
        return None

    import semabridge.api.routers.export_router as export_router

    monkeypatch.setattr(export_router, "_load_osi_model", fake_load)
    res = client.post("/api/ontology/domains/from-osi", json={"domain_name": "Finance"})
    assert seen.get("project_id") == "proj-42"
    assert res.status_code == 404  # fake loader returns no model
