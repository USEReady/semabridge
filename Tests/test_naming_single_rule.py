"""One naming rule for every place that computes or compares a deployed name.

The table in frontend/src/utils/snowflakeNamingTable.json was generated from
``snowflake_target_name``; this file asserts the backend still produces it,
and frontend/src/utils/collisionResolution.test.js asserts the JS mirror does
too, so both sides are pinned to one table.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from semabridge.utils.identifiers import (
    SNOWFLAKE_ALIAS_RESERVED_WORDS,
    SNOWFLAKE_RESERVED_WORDS,
    IdentifierSanitizer,
    snowflake_target_name,
)

_TABLE_PATH = Path(__file__).resolve().parents[1] / "frontend" / "src" / "utils" / "snowflakeNamingTable.json"


def _table():
    return json.loads(_TABLE_PATH.read_text(encoding="utf-8"))


# ── 3. shared table (backend side) + preview endpoint ──────────────────────

@pytest.mark.parametrize("kind", ["column", "metric"])
def test_backend_matches_the_shared_naming_table(kind):
    for name, expected in _table()[kind].items():
        assert snowflake_target_name(name, entity_kind=kind) == expected, (kind, name)


def test_shared_table_pins_both_reserved_lists():
    table = _table()
    assert table["reserved_words"]["column"] == sorted(SNOWFLAKE_ALIAS_RESERVED_WORDS)
    assert table["reserved_words"]["metric"] == sorted(SNOWFLAKE_RESERVED_WORDS)


def test_naming_preview_endpoint_uses_the_one_rule():
    from semabridge.api.routers.naming_router import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.get("/api/naming/preview", params={"name": "Date", "kind": "column"}).json() == {"name": "DATE"}
    assert client.get("/api/naming/preview", params={"name": "Date", "kind": "metric"}).json() == {"name": "COL_DATE"}
    assert client.get(
        "/api/naming/preview", params={"name": "Sales %", "kind": "column", "target": "snowflake"},
    ).json() == {"name": "SALES_PCT"}
    assert client.get("/api/naming/preview", params={"name": "x", "kind": "table"}).status_code == 422


def test_naming_router_is_registered():
    import inspect

    from semabridge.api.bootstrap import router_registry

    assert "naming_router" in inspect.getsource(router_registry.register_routers)


def test_duplicate_reserved_lists_use_the_identifiers_lists():
    from semabridge.converter.query_assembly import QueryValidator
    from semabridge.core.validation.cortex_validator import CortexValidator

    alias_upper = {w.upper() for w in SNOWFLAKE_ALIAS_RESERVED_WORDS}
    assert QueryValidator.RESERVED_KEYWORDS == alias_upper
    assert alias_upper <= CortexValidator.CORTEX_RESERVED


def test_auto_map_reserved_check_is_kind_aware():
    from semabridge.api.services.mapping_service import _compat_serialize_auto_map_entity_mappings

    rows = _compat_serialize_auto_map_entity_mappings(
        [
            {"id": "a", "source_name": "Date", "target_name": "DATE", "entity_kind": "column",
             "source_path": "datasets.Sales.columns.Date"},
            {"id": "b", "source_name": "Order", "target_name": "ORDER", "entity_kind": "column",
             "source_path": "datasets.Sales.columns.Order"},
            {"id": "c", "source_name": "Date", "target_name": "DATE", "entity_kind": "metric",
             "source_path": "metrics.Date"},
        ],
        target_connector="snowflake",
    )
    by_id = {r["id"]: r for r in rows}
    assert by_id["a"]["validation_code"] != "RESERVED_KEYWORD"
    assert by_id["b"]["validation_code"] == "RESERVED_KEYWORD"
    assert by_id["b"]["suggested_target_name"] == "COL_ORDER"
    assert by_id["c"]["validation_code"] == "RESERVED_KEYWORD"
    assert by_id["c"]["suggested_target_name"] == "COL_DATE"


def test_default_target_identifier_is_snowflake_target_name():
    from semabridge.api.services.project_mapping_engine import _default_target_identifier

    for name in ("Date", "Value", "Order", "Sales %", "Left"):
        assert _default_target_identifier(name, "snowflake", "column") == snowflake_target_name(name, entity_kind="column")
        assert _default_target_identifier(name, "snowflake", "metric") == snowflake_target_name(name, entity_kind="metric")


# ── 2. context-restricted words keep COL_ for dimensions ───────────────────

@pytest.mark.parametrize("word", [
    "Account", "Case", "Cast", "Connection", "Constraint", "Cross", "Database", "False", "Full",
    "GSCluster", "Inner", "Issue", "Join", "Lateral", "Left", "Natural", "Organization", "Right",
    "Schema", "True", "Try_Cast", "Using", "View", "When",
])
def test_context_reserved_words_get_col_prefix_for_dimensions(word):
    assert IdentifierSanitizer().sanitize_semantic_alias(word) == f"COL_{word.upper()}"


@pytest.mark.parametrize("word", ["Date", "Key", "Value", "Count", "Number", "Timestamp", "User", "Role"])
def test_type_words_stay_unprefixed_for_dimensions(word):
    assert IdentifierSanitizer().sanitize_semantic_alias(word) == word.upper()


# ── 1. the sync gate applies the same rule as the sync ────────────────────

def _row(path, target, table="Sales", kind="column"):
    return {
        "id": path, "source_path": path, "target_name": target, "entity_kind": kind,
        "parent_source_path": f"datasets.{table}", "status": "auto",
    }


def test_plan_blockers_reports_a_rename_that_clashes_with_an_existing_column():
    from semabridge.api.services.dry_run_job_service import _plan_blockers

    result = {"entity_mappings": [
        _row("datasets.Sales.columns.Value", "VALUE"),
        _row("datasets.Sales.columns.Amount", "AMOUNT"),
    ]}
    overrides = [{"source_path": "datasets.Sales.columns.Amount", "target_name": "Value"}]
    issues = _plan_blockers(result, overrides, "snowflake")
    assert any("duplicate target name" in i and "VALUE" in i for i in issues), issues


def test_plan_blockers_ignores_a_saved_legacy_auto_name():
    from semabridge.api.services.dry_run_job_service import _plan_blockers

    # The sync ignores a saved COL_DATE for 'Date' and deploys DATE; a real
    # COL_DATE column must therefore not be reported as clashing with it.
    result = {"entity_mappings": [
        _row("datasets.Sales.columns.Date", "DATE"),
        _row("datasets.Sales.columns.COL_DATE", "COL_DATE"),
    ]}
    overrides = [{"source_path": "datasets.Sales.columns.Date", "target_name": "COL_DATE"}]
    assert _plan_blockers(result, overrides, "snowflake") == []


def test_plan_blockers_metric_overrides_keep_to_alias():
    from semabridge.api.services.dry_run_job_service import _plan_blockers

    result = {"entity_mappings": [
        _row("metrics.Total", "TOTAL", kind="metric"),
        _row("metrics.Other", "COL_VALUE", kind="metric"),
    ]}
    overrides = [{"source_path": "metrics.Total", "target_name": "Value"}]
    issues = _plan_blockers(result, overrides, "snowflake")
    assert any("COL_VALUE" in i for i in issues), issues


# ── 6. deployed name changes are marked in the dry run ────────────────────

_DEPLOYED_DDL = """CREATE OR REPLACE SEMANTIC VIEW DB.SC.SALES_VIEW
TABLES ( SALES AS DB.SC.SALES PRIMARY KEY (ID) )
FACTS ( SALES."AMOUNT" AS SALES."AMOUNT" )
DIMENSIONS (
  SALES."COL_DATE" AS SALES."COL_DATE" WITH SYNONYMS = ('day'),
  SALES."REGION" AS SALES."REGION",
  SALES."COL_VALUE" AS SALES."COL_VALUE"
)
METRICS ( SALES.TOTAL AS SUM(SALES."AMOUNT") );
"""


def _dry_rows():
    return [
        {"entity_kind": "column", "source_name": "Date", "source_table": "Sales", "target_name": "DATE"},
        {"entity_kind": "column", "source_name": "Region", "source_table": "Sales", "target_name": "REGION"},
        {"entity_kind": "column", "source_name": "Value", "source_table": "Sales", "target_name": "VALUE"},
        {"entity_kind": "column", "source_name": "Amount", "source_table": "Sales", "target_name": "AMOUNT"},
        {"entity_kind": "measure", "source_name": "Total", "source_table": "Sales", "target_name": "TOTAL"},
    ]


def test_dry_run_rows_mark_names_that_change_in_snowflake(tmp_path):
    from semabridge.api.services.deployed_name_changes import annotate_dry_run_rows

    view_dir = tmp_path / "reverse" / "proj-1"
    view_dir.mkdir(parents=True)
    (view_dir / "semantic_view.sql").write_text(_DEPLOYED_DDL, encoding="utf-8")
    rows = _dry_rows()
    assert annotate_dry_run_rows(rows, "proj-1", root=tmp_path) is True
    by_name = {r["source_name"]: r for r in rows}
    assert by_name["Date"]["deployed_name_change"] == {"from": "COL_DATE", "to": "DATE"}
    assert by_name["Value"]["deployed_name_change"] == {"from": "COL_VALUE", "to": "VALUE"}
    for unchanged in ("Region", "Amount", "Total"):
        assert "deployed_name_change" not in by_name[unchanged]


def test_dry_run_rows_unknown_without_a_deploy(tmp_path):
    from semabridge.api.services.deployed_name_changes import annotate_dry_run_rows

    rows = _dry_rows()
    assert annotate_dry_run_rows(rows, "never-deployed", root=tmp_path) is False
    assert not any("deployed_name_change" in r for r in rows)


def test_multi_model_ddl_is_read_from_the_namespace_dir(tmp_path):
    from semabridge.api.services.deployed_name_changes import annotate_dry_run_rows

    view_dir = tmp_path / "reverse" / "proj-2" / "M1"
    view_dir.mkdir(parents=True)
    (view_dir / "semantic_view.sql").write_text(_DEPLOYED_DDL, encoding="utf-8")
    rows = _dry_rows()
    assert annotate_dry_run_rows(rows, "proj-2", "M1", root=tmp_path) is True
    assert rows[0]["deployed_name_change"] == {"from": "COL_DATE", "to": "DATE"}


# ── 7. ontology / Ask AI use the dimension name ───────────────────────────

def test_ontology_dimension_logical_name_uses_the_semantic_rule():
    from semabridge.intermediate.models import OSIColumn, OSIDataset, OSIModel
    from semabridge.ontology.federated_loader import FederatedOntologyLoader

    model = OSIModel(
        unique_name="M", label="M",
        datasets=[OSIDataset(unique_name="Sales", columns=[OSIColumn(unique_name="Date"), OSIColumn(unique_name="Order")])],
    )
    onto = FederatedOntologyLoader.from_osi_model(model, domain_name="D")
    concept = next(c for c in onto.concepts if c.name == "Sales")
    names = {a.name: a.logical_name for a in concept.attributes}
    assert names == {"Date": "DATE", "Order": "COL_ORDER"}
    assert concept.logical_name == "SALES"

    # A stored legacy COL_DATE is replaced on re-sync; a user's own name is kept.
    concept.attributes[0].logical_name = "COL_DATE"
    concept.attributes[1].logical_name = "ORDER_NO"
    again = FederatedOntologyLoader.from_osi_model(model, domain_name="D", existing_ontology=onto)
    names = {a.name: a.logical_name for a in next(c for c in again.concepts if c.name == "Sales").attributes}
    assert names == {"Date": "DATE", "Order": "ORDER_NO"}


def test_sv_dim_ref_does_not_trust_a_stale_col_logical_name():
    from semabridge.ai.nl_query_engine import NLQueryEngine

    lexicon = SimpleNamespace(dimensions=[])
    concept = SimpleNamespace(name="Sales", logical_name="SALES")
    stale = SimpleNamespace(name="Date", logical_name="COL_DATE")
    assert NLQueryEngine._sv_dim_ref(concept, stale, lexicon) == "SALES.DATE"
    custom = SimpleNamespace(name="Date", logical_name="ORDER_DAY")
    assert NLQueryEngine._sv_dim_ref(concept, custom, lexicon) == "SALES.ORDER_DAY"
    # The alias recorded from the deployed view wins over everything.
    deployed = SimpleNamespace(
        sv_dim_alias=("S", "COL_DATE"),
        concept=SimpleNamespace(name="Sales"),
        attribute=SimpleNamespace(name="Date"),
    )
    assert NLQueryEngine._sv_dim_ref(concept, stale, SimpleNamespace(dimensions=[deployed])) == "S.COL_DATE"


# ── 8. relationship names are stable across the naming-rule change ────────

def test_relationship_names_keep_the_old_rule():
    from semabridge.connectors.semantic_ddl_sanitizer import SemanticDDLSanitizer
    from semabridge.connectors.snowflake_emitter_parts.identifier_utilities import (
        sanitize_semantic_name,
        to_snowflake_relationship_name,
    )

    sanitizer = IdentifierSanitizer()
    ddl = SemanticDDLSanitizer(sanitizer)
    emitter = SimpleNamespace(_id=sanitizer)
    for fn in (ddl.to_snowflake_relationship_name, lambda n: to_snowflake_relationship_name(emitter, n)):
        assert fn("Date") == "COL_DATE"          # was COL_DATE before the rule change
        assert fn("REL_Sales_Date") == "SALES_DATE"
        assert fn("2024 Link") == "_2024_LINK"
    # Dimension names do use the new rule.
    assert ddl.sanitize_semantic_name("Date") == "DATE"
    assert sanitize_semantic_name(emitter, "Date") == "DATE"
