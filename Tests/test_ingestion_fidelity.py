"""Semantic fidelity of BI-model ingestion (TMSL / model.bim / .pbix -> OSI -> SML).

Every source construct must either be carried into the intermediate model or
recorded in the DropLedger with a specific reason. Nothing silently lost,
nothing invented. All fixtures are synthetic.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import pytest

from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.converter.tmsl_to_osi import (
    INFERRED_RELATIONSHIP_REASON,
    TMSLToOSIConverter,
    classify_tmsl_table,
)
from semabridge.core.drop_ledger import DropLedger, DropStage
from semabridge.intermediate.models import (
    OSICardinality,
    OSIColumn,
    OSICrossFilterDirection,
    OSIDataset,
    OSIDimension,
    OSIAttribute,
    OSIModel,
    OSIRelationship,
)


def _convert(model: Dict[str, Any], **source_extra: Any):
    ledger = DropLedger()
    osi = TMSLToOSIConverter(drop_ledger=ledger).to_osi(
        {
            "tmsl": {"model": copy.deepcopy(model)},
            "dataset_id": "fidelity",
            "project_id": "zz_fidelity_test",
            "synonym_overrides": {},
            **source_extra,
        }
    )
    return osi, ledger


def _records(ledger: DropLedger, kind: str) -> List[Any]:
    return [r for r in ledger.records if r.entity_kind == kind]


def _col(name: str, **kw: Any) -> Dict[str, Any]:
    return {"name": name, "dataType": kw.pop("dataType", "int64"), "sourceColumn": name, **kw}


BASE_TABLES = [
    {"name": "Fact", "columns": [_col("CustKey"), _col("Amount", dataType="double")]},
    {"name": "Customer", "columns": [_col("CustKey"), _col("Name", dataType="string")]},
]


# ---------------------------------------------------------------------------
# Defect 1: relationship cardinality / cross-filter / isActive
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "rel_props, expected",
    [
        ({}, OSICardinality.MANY_TO_ONE),  # TMSL defaults: many -> one
        ({"fromCardinality": "one", "toCardinality": "one"}, OSICardinality.ONE_TO_ONE),
        ({"fromCardinality": "many", "toCardinality": "many"}, OSICardinality.MANY_TO_MANY),
        ({"fromCardinality": "one", "toCardinality": "many"}, OSICardinality.ONE_TO_MANY),
        ({"toCardinality": "many"}, OSICardinality.MANY_TO_MANY),  # fromCardinality defaults to many
        ({"cardinality": "OneToOne"}, OSICardinality.ONE_TO_ONE),  # combined legacy form
    ],
)
def test_relationship_cardinality_reads_per_end_tmsl_properties(rel_props, expected):
    rel = {"name": "r", "fromTable": "Fact", "fromColumn": "CustKey",
           "toTable": "Customer", "toColumn": "CustKey", **rel_props}
    osi, _ = _convert({"name": "M", "tables": BASE_TABLES, "relationships": [rel]})
    assert len(osi.relationships) == 1
    assert osi.relationships[0].cardinality == expected


@pytest.mark.parametrize(
    "behavior, expected",
    [
        ("oneDirection", OSICrossFilterDirection.SINGLE),
        ("bothDirections", OSICrossFilterDirection.BOTH),
        ("automatic", OSICrossFilterDirection.SINGLE),
        ("Both", OSICrossFilterDirection.BOTH),
    ],
)
def test_relationship_cross_filter_mapping(behavior, expected):
    rel = {"fromTable": "Fact", "fromColumn": "CustKey", "toTable": "Customer",
           "toColumn": "CustKey", "crossFilteringBehavior": behavior, "isActive": False}
    osi, _ = _convert({"name": "M", "tables": BASE_TABLES, "relationships": [rel]})
    assert osi.relationships[0].cross_filter_direction == expected
    assert osi.relationships[0].is_active is False


def test_legacy_tmsl_to_sml_reads_per_end_cardinality():
    from semabridge.converter.tmsl_to_sml import TMSLTransformer
    from semabridge.sml.models import Cardinality

    rel = {"fromTable": "Fact", "fromColumn": "CustKey", "toTable": "Customer",
           "toColumn": "CustKey", "fromCardinality": "one", "toCardinality": "one"}
    parsed = TMSLTransformer()._parse_relationship(rel, None)
    assert parsed.cardinality == Cardinality.ONE_TO_ONE


# ---------------------------------------------------------------------------
# Defect 2: no invented relationships by default
# ---------------------------------------------------------------------------

INFER_TABLES = [
    {"name": "Orders", "columns": [_col("OrderNo"), _col("Customer_Code")]},
    {"name": "Customers", "columns": [_col("ID"), _col("Name", dataType="string")]},
]


def test_relationship_inference_is_off_by_default(monkeypatch):
    monkeypatch.delenv("SEMABRIDGE_INFER_RELATIONSHIPS", raising=False)
    osi, ledger = _convert({"name": "M", "tables": INFER_TABLES})
    assert osi.relationships == []
    assert not [r for r in ledger.records if r.reason == INFERRED_RELATIONSHIP_REASON]


def test_relationship_inference_opt_in_flags_and_ledgers_inferred_joins():
    osi, ledger = _convert({"name": "M", "tables": INFER_TABLES}, infer_relationships=True)
    assert osi.relationships, "opt-in inference should find Customer_Code -> Customers.ID"
    assert all(r.is_inferred for r in osi.relationships)
    inferred = [r for r in ledger.records if r.reason == INFERRED_RELATIONSHIP_REASON]
    assert len(inferred) == len(osi.relationships)
    assert all(r.by_design and r.entity_kind == "relationship" for r in inferred)
    sml = OSIToSMLConverter().from_osi(osi)
    assert all(r.is_inferred for r in sml.relationships)


def test_explicit_false_overrides_environment(monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_INFER_RELATIONSHIPS", "1")
    osi, _ = _convert({"name": "M", "tables": INFER_TABLES}, infer_relationships=False)
    assert osi.relationships == []


# ---------------------------------------------------------------------------
# Defect 3: calculated tables / field parameters / calculation groups
# ---------------------------------------------------------------------------

CALENDAR = {
    "name": "Calendar",
    "columns": [{"name": "Date", "dataType": "dateTime", "type": "calculatedTableColumn", "sourceColumn": "[Date]"}],
    "partitions": [{"name": "Calendar", "source": {"type": "calculated", "expression": ["CALENDAR(", "DATE(2020,1,1), DATE(2020,12,31))"]}}],
}
FIELD_PARAM = {
    "name": "Metric Picker",
    "columns": [{"name": "Metric Picker", "dataType": "string", "type": "calculatedTableColumn", "sourceColumn": "[Value1]"}],
    "partitions": [{"name": "p", "source": {"type": "calculated",
                                            "expression": "{(\"Amount\", NAMEOF('Fact'[Amount]), 0)}"}}],
}
CALC_GROUP = {
    "name": "Time Calc",
    "calculationGroup": {"precedence": 10, "calculationItems": [
        {"name": "YTD", "expression": "CALCULATE(SELECTEDMEASURE(), DATESYTD('Calendar'[Date]))"},
        {"name": "PY", "expression": "CALCULATE(SELECTEDMEASURE(), SAMEPERIODLASTYEAR('Calendar'[Date]))"},
    ]},
    "columns": [{"name": "Time Calc", "dataType": "string", "sourceColumn": "Name"}],
    "partitions": [{"name": "p", "source": {"type": "calculationGroup"}}],
}


def test_classify_tmsl_table_kinds():
    assert classify_tmsl_table(BASE_TABLES[0]) == ("table", None)
    kind, expr = classify_tmsl_table(CALENDAR)
    assert kind == "calculated_table" and expr.startswith("CALENDAR(")
    assert classify_tmsl_table(FIELD_PARAM)[0] == "field_parameter"
    assert classify_tmsl_table(CALC_GROUP)[0] == "calculation_group"


def test_calculated_table_marked_and_ledgered():
    osi, ledger = _convert({"name": "M", "tables": BASE_TABLES + [CALENDAR]})
    cal = osi.get_dataset("Calendar")
    assert cal.dataset_kind == "calculated_table" and not cal.is_physical
    assert cal.expression == "CALENDAR(\nDATE(2020,1,1), DATE(2020,12,31))"
    assert osi.get_dataset("Fact").dataset_kind == "table"
    rec = [r for r in _records(ledger, "table") if r.entity_name == "Calendar"]
    assert len(rec) == 1
    assert rec[0].by_design is True and rec[0].stage == DropStage.EXTRACTION
    assert rec[0].reason == (
        "calculated table — not materialized in Snowflake; materialize it as a table/view to include it"
    )
    assert "CALENDAR(" in rec[0].detail
    sml = OSIToSMLConverter().from_osi(osi)
    sml_cal = next(d for d in sml.datasets if d.unique_name == "Calendar")
    assert sml_cal.dataset_kind == "calculated_table"
    assert sml_cal.expression.startswith("CALENDAR(")


def test_field_parameter_marked_and_ledgered():
    osi, ledger = _convert({"name": "M", "tables": BASE_TABLES + [FIELD_PARAM]})
    assert osi.get_dataset("Metric Picker").dataset_kind == "field_parameter"
    rec = [r for r in _records(ledger, "table") if r.entity_name == "Metric Picker"][0]
    assert rec.by_design and "field parameter" in rec.reason


def test_calculation_group_excluded_carried_and_ledgered():
    osi, ledger = _convert({"name": "M", "tables": BASE_TABLES + [CALC_GROUP]})
    assert osi.get_dataset("Time Calc") is None
    groups = osi.metadata["calculation_groups"]
    assert groups[0]["name"] == "Time Calc" and groups[0]["precedence"] == 10
    assert [i["name"] for i in groups[0]["items"]] == ["YTD", "PY"]
    rec = [r for r in _records(ledger, "table") if r.entity_name == "Time Calc"][0]
    assert rec.by_design is True
    assert rec.reason == (
        "calculation group — Snowflake semantic views have no calculation groups; "
        "2 calculation item(s) not migrated (YTD, PY)"
    )
    assert "SELECTEDMEASURE" in rec.detail


# ---------------------------------------------------------------------------
# Defect 4: calculated columns + column/measure metadata (TMSL and binary path)
# ---------------------------------------------------------------------------

def test_calculated_column_expression_split_from_source_column():
    table = {"name": "Fact", "columns": [
        _col("Amount", dataType="double", formatString="#,0.00", displayFolder="Money",
             dataCategory="Currency", description="Net amount"),
        {"name": "Double", "dataType": "double", "type": "calculated", "expression": ["Fact[Amount]", "* 2"]},
    ]}
    osi, _ = _convert({"name": "M", "tables": [table]})
    ds = osi.get_dataset("Fact")
    amount, double = ds.get_column("Amount"), ds.get_column("Double")
    assert amount.source_column == "Amount" and not amount.is_calculated and amount.expression is None
    assert amount.format_string == "#,0.00" and amount.folder == "Money"
    assert amount.data_category == "Currency" and amount.description == "Net amount"
    assert double.is_calculated and double.source_column is None
    assert double.expression == "Fact[Amount]\n* 2"
    # legacy combined field kept for existing consumers
    assert double.source_expression == "Fact[Amount]\n* 2"
    sml = OSIToSMLConverter().from_osi(osi)
    sml_double = next(d for d in sml.datasets if d.unique_name == "Fact").get_column("Double")
    assert sml_double.is_calculated and sml_double.expression == "Fact[Amount]\n* 2"
    sml_amount = next(d for d in sml.datasets if d.unique_name == "Fact").get_column("Amount")
    assert sml_amount.folder == "Money" and sml_amount.data_category == "Currency"


def test_measure_metadata_carried():
    table = {"name": "Fact", "columns": [_col("Amount", dataType="double")], "measures": [
        {"name": "Total", "expression": "SUM(Fact[Amount])", "formatString": "$#,0",
         "isHidden": True, "displayFolder": "KPIs", "description": "d"},
    ]}
    osi, _ = _convert({"name": "M", "tables": [table]})
    m = osi.get_metric("Total")
    assert (m.format_string, m.is_hidden, m.folder, m.description) == ("$#,0", True, "KPIs", "d")


def _connector() -> LocalPBIXConnector:
    conn = LocalPBIXConnector.__new__(LocalPBIXConnector)
    conn._pbix_path = Path("synthetic.pbix")
    conn.drop_ledger = DropLedger()
    return conn


def test_binary_fallback_tmsl_carries_calculated_columns_partitions_and_measure_metadata():
    conn = _connector()
    fallback = {
        "models": [{"name": "M"}],
        "tables": [
            {"name": "Fact", "columns": [
                {"name": "Amount", "data_type": "double", "source_column": "Amount", "type": "data",
                 "format_string": "#,0", "display_folder": "F", "sort_by_column": "Key",
                 "description": "amt", "data_category": "Currency"},
                {"name": "Key", "data_type": "int64", "source_column": "Key", "type": "data"},
                {"name": "Twice", "data_type": "double", "type": "calculated", "expression": "Fact[Amount]*2"},
            ], "partitions": [{"name": "p", "source_type": "m", "expression": "let x = 1 in x"}]},
            {"name": "Calendar", "columns": [
                {"name": "Date", "data_type": "dateTime", "source_column": "[Date]", "type": "calculatedTableColumn"},
            ], "partitions": [{"name": "c", "source_type": "calculated", "expression": "CALENDARAUTO()"}]},
        ],
        "measures": [{"name": "Total", "table": "Fact", "expression": "SUM(Fact[Amount])",
                      "format_string": "#,0", "is_hidden": True, "display_folder": "K",
                      "kpi": {"targetExpression": "100", "statusExpression": "1"}}],
        "relationships": [],
        "hierarchies": {"Fact": [{"name": "H", "levels": [{"name": "K", "column": "Key", "ordinal": 0}]}]},
        "roles": [{"name": "EU", "modelPermission": "read",
                   "tablePermissions": [{"name": "Fact", "filterExpression": "Fact[Key] = 1"}]}],
        "perspectives": [{"name": "Sales", "tables": [{"name": "Fact", "columns": []}]}],
        "cultures": [{"name": "en-US", "linguisticMetadata": {"content": json.dumps({"Entities": {
            "fact.amount": {"Definition": {"Binding": {"ConceptualEntity": "Fact", "ConceptualProperty": "Amount"}},
                            "Terms": [{"revenue": {}}]}}})}}],
    }
    tmsl = conn._build_tmsl_from_fallback(fallback)
    model = tmsl["model"]
    fact = next(t for t in model["tables"] if t["name"] == "Fact")
    twice = next(c for c in fact["columns"] if c["name"] == "Twice")
    assert twice["type"] == "calculated" and twice["expression"] == "Fact[Amount]*2"
    assert "sourceColumn" not in twice
    amount = next(c for c in fact["columns"] if c["name"] == "Amount")
    assert amount["formatString"] == "#,0" and amount["sortByColumn"] == "Key"
    assert fact["partitions"][0]["source"]["expression"] == "let x = 1 in x"
    measure = fact["measures"][0]
    assert measure["formatString"] == "#,0" and measure["isHidden"] is True and measure["kpi"]["targetExpression"] == "100"
    cal = next(t for t in model["tables"] if t["name"] == "Calendar")
    assert cal["partitions"][0]["source"] == {"type": "calculated", "expression": "CALENDARAUTO()"}

    osi, ledger = _convert(model)
    assert osi.get_dataset("Fact").get_column("Twice").is_calculated
    assert osi.get_dataset("Calendar").dataset_kind == "calculated_table"
    assert osi.get_metric("Total").kpi["target_expression"] == "100"
    assert "revenue" in osi.get_dataset("Fact").get_column("Amount").synonyms
    assert _records(ledger, "security_role") and _records(ledger, "perspective")
    assert _records(ledger, "hierarchy") and _records(ledger, "kpi")


class _FakeSQLite:
    def __init__(self, frames: Dict[str, pd.DataFrame]):
        self.frames = frames

    def execute_query(self, sql: str) -> pd.DataFrame:
        flat = " ".join(sql.split())
        for needle, df in self.frames.items():
            if needle in flat:
                return df.copy()
        return pd.DataFrame()


def test_raw_catalog_extras_read_measure_kpi_hierarchy_roles_calc_groups():
    conn = _connector()
    sh = _FakeSQLite({
        "FROM KPI": pd.DataFrame([{"MeasureID": 7, "TargetExpression": "[Goal]", "StatusExpression": "1"}]),
        "FROM Measure": pd.DataFrame([{"ID": 7, "TableID": 1, "Name": "Sales", "Expression": "SUM(F[A])",
                                       "FormatString": "#,0", "IsHidden": 1, "DisplayFolder": "D",
                                       "Description": None}]),
        "FROM Hierarchy": pd.DataFrame([{"ID": 3, "TableID": 1, "Name": "H", "IsHidden": 0}]),
        "FROM Level": pd.DataFrame([{"HierarchyID": 3, "Ordinal": 1, "Name": "L2", "ColumnID": 11},
                                    {"HierarchyID": 3, "Ordinal": 0, "Name": "L1", "ColumnID": 10}]),
        "FROM Role": pd.DataFrame([{"ID": 1, "Name": "R", "ModelPermission": 2}]),
        "FROM TablePermission": pd.DataFrame([{"ID": 5, "RoleID": 1, "TableID": 1,
                                               "FilterExpression": "F[A] > 0", "MetadataPermission": 1}]),
        "FROM ColumnPermission": pd.DataFrame([{"TablePermissionID": 5, "ColumnID": 11, "MetadataPermission": 2}]),
        "FROM CalculationGroup": pd.DataFrame([{"ID": 9, "TableID": 2, "Precedence": 1}]),
        "FROM CalculationItem": pd.DataFrame([{"CalculationGroupID": 9, "Name": "YTD",
                                               "Expression": "SELECTEDMEASURE()", "Ordinal": 0}]),
    })
    extras = conn._extract_raw_catalog_extras(
        sh, {1: "F", 2: "CG"}, {10: ("F", "A"), 11: ("F", "B")}
    )
    m = extras["measures"][0]
    assert (m["format_string"], m["is_hidden"], m["display_folder"]) == ("#,0", True, "D")
    assert m["kpi"]["targetExpression"] == "[Goal]"
    assert [lv["name"] for lv in extras["hierarchies"]["F"][0]["levels"]] == ["L1", "L2"]
    role = extras["roles"][0]
    assert role["tablePermissions"][0]["filterExpression"] == "F[A] > 0"
    assert role["tablePermissions"][0]["columnPermissions"] == [{"name": "B", "metadataPermission": "none"}]
    assert extras["calculation_groups"]["CG"]["calculationItems"][0]["name"] == "YTD"


# ---------------------------------------------------------------------------
# Defect 5: relationships to missing / excluded tables are ledgered
# ---------------------------------------------------------------------------

def test_relationship_to_missing_table_is_ledgered():
    rel = {"fromTable": "Fact", "fromColumn": "CustKey", "toTable": "Ghost", "toColumn": "CustKey"}
    osi, ledger = _convert({"name": "M", "tables": BASE_TABLES, "relationships": [rel]})
    assert osi.relationships == []
    rec = _records(ledger, "relationship")
    assert len(rec) == 1
    assert rec[0].entity_name == "Fact[CustKey] -> Ghost[CustKey]"
    assert rec[0].by_design is False
    assert rec[0].reason == (
        "relationship references table(s) not in the semantic model — dropped: "
        "'Ghost' (not found in the source model)"
    )


def test_relationship_to_by_design_excluded_table_is_by_design():
    tables = BASE_TABLES + [{"name": "LocalDateTable_abc", "columns": [_col("Date", dataType="dateTime")]}]
    rel = {"fromTable": "Fact", "fromColumn": "CustKey", "toTable": "LocalDateTable_abc", "toColumn": "Date"}
    _, ledger = _convert({"name": "M", "tables": tables, "relationships": [rel]})
    rec = _records(ledger, "relationship")[0]
    assert rec.by_design is True and "excluded:" in rec.reason


def test_unparseable_relationship_is_ledgered():
    _, ledger = _convert({"name": "M", "tables": BASE_TABLES, "relationships": [{"fromTable": "Fact"}]})
    rec = _records(ledger, "relationship")[0]
    assert rec.by_design is False and "could not be parsed" in rec.reason


# ---------------------------------------------------------------------------
# Defect 6: hierarchies, roles, perspectives, KPIs, sort-by, linguistic schema
# ---------------------------------------------------------------------------

FULL_MODEL = {
    "name": "M",
    "tables": [
        {"name": "Date", "columns": [_col("Year"), _col("MonthName", dataType="string", sortByColumn="MonthNo"),
                                     _col("MonthNo")],
         "hierarchies": [{"name": "Calendar", "levels": [
             {"name": "Month", "ordinal": 1, "column": "MonthName"},
             {"name": "Year", "ordinal": 0, "column": "Year"}]}]},
        {"name": "Fact", "columns": [_col("Amount", dataType="double")],
         "measures": [{"name": "Sales", "expression": "SUM(Fact[Amount])",
                       "kpi": {"targetExpression": "1000", "statusExpression": "IF([Sales]>1000,1,-1)",
                               "statusGraphic": "Traffic Light"}}]},
    ],
    "roles": [
        {"name": "Europe", "modelPermission": "read", "tablePermissions": [
            {"name": "Fact", "filterExpression": "Fact[Amount] > 0",
             "columnPermissions": [{"name": "Amount", "metadataPermission": "none"}]}]},
        {"name": "Admin", "modelPermission": "administrator"},
    ],
    "perspectives": [{"name": "Exec", "tables": [{"name": "Fact", "measures": [{"name": "Sales"}]}]}],
    "cultures": [{"name": "en-US", "linguisticMetadata": {"content": {"Entities": {
        "fact.sales": {"Definition": {"Binding": {"ConceptualEntity": "Fact", "ConceptualProperty": "Sales"}},
                       "Terms": [{"revenue": {}}, {"sale": {"State": "Generated"}},
                                 {"turnover": {"State": "Suggested"}}, {"gone": {"State": "Deleted"}}]},
        "date.year": {"Definition": {"Binding": {"ConceptualEntity": "Date", "ConceptualProperty": "Year"}},
                      "Terms": [{"fiscal year": {}}]},
    }}}}],
}


def test_hierarchies_carried_and_ledgered():
    osi, ledger = _convert(FULL_MODEL)
    dim = osi.get_dimension("Date")
    h = dim.hierarchies[0]
    assert h.unique_name == "Calendar"
    assert [(lv.unique_name, lv.attribute) for lv in h.levels] == [("Year", "Year"), ("Month", "MonthName")]
    rec = _records(ledger, "hierarchy")[0]
    assert rec.by_design and rec.dataset == "Date"
    assert rec.reason == (
        "hierarchy 'Calendar' (Year [Year] > Month [MonthName]) not migrated as a drill path — "
        "Snowflake semantic views have no hierarchy object; its level columns are still emitted "
        "as individual dimensions"
    )
    sml = OSIToSMLConverter().from_osi(osi)
    sml_dim = next(d for d in sml.dimensions if d.unique_name == "Date")
    assert [lv.attribute for lv in sml_dim.hierarchies[0].levels] == ["Year", "MonthName"]


def test_rls_roles_are_prominent_ledger_entries_and_carried():
    osi, ledger = _convert(FULL_MODEL)
    recs = {r.entity_name: r for r in _records(ledger, "security_role")}
    assert set(recs) == {"Europe", "Admin"}
    europe = recs["Europe"]
    assert europe.by_design is False  # security gaps must never be de-emphasized
    assert europe.reason.startswith(
        "row-level security role 'Europe' not migrated — Snowflake semantic views do not carry RLS; "
        "implement with a Snowflake row access policy. Filter: Fact: Fact[Amount] > 0"
    )
    assert "column 'Fact'[Amount] hidden" in europe.reason
    assert "(no row filters defined)" in recs["Admin"].reason
    carried = {r["name"]: r for r in osi.metadata["security_roles"]}
    assert carried["Europe"]["table_permissions"][0]["filter_expression"] == "Fact[Amount] > 0"


def test_perspectives_kpis_and_sort_by_ledgered():
    osi, ledger = _convert(FULL_MODEL)
    persp = _records(ledger, "perspective")[0]
    assert persp.entity_name == "Exec" and persp.by_design
    assert osi.metadata["perspectives"][0]["tables"][0]["measures"] == ["Sales"]

    kpi = _records(ledger, "kpi")[0]
    assert kpi.by_design and kpi.dataset == "Fact"
    assert kpi.reason.startswith("KPI on measure 'Sales' not migrated")
    assert "Target: 1000" in kpi.reason and "Status: IF([Sales]>1000,1,-1)" in kpi.reason
    assert osi.get_metric("Sales").kpi["status_graphic"] == "Traffic Light"

    sort = _records(ledger, "column_property")[0]
    assert sort.dataset == "Date" and sort.by_design
    assert osi.get_dataset("Date").get_column("MonthName").sort_by_column == "MonthNo"
    assert "order by 'MonthNo'" in sort.reason


def test_linguistic_schema_synonyms_merged():
    osi, _ = _convert(FULL_MODEL)
    sales = osi.get_metric("Sales")
    assert "revenue" in sales.synonyms and sales.synonym_sources["revenue"] == "tmsl_authored"
    assert "sale" in sales.synonyms and sales.synonym_sources["sale"] == "auto_generated"
    assert "turnover" not in sales.synonyms and "gone" not in sales.synonyms
    assert "fiscal year" in osi.get_dataset("Date").get_column("Year").synonyms


def test_reviewer_probe_model_ledger_is_complete():
    """The reviewer's acceptance probe: nothing silently lost."""
    tmsl = {"name": "M", "tables": [
        {"name": "A", "columns": [
            {"name": "K", "dataType": "int64", "sourceColumn": "K"},
            {"name": "C", "type": "calculated", "dataType": "string", "expression": "A[K]&\"x\"",
             "displayFolder": "F", "sortByColumn": "K"}],
         "hierarchies": [{"name": "H", "levels": [{"name": "L", "column": "K"}]}],
         "measures": [{"name": "m", "expression": "SUM(A[K])", "kpi": {"targetExpression": "1"}}]},
        {"name": "B", "columns": [{"name": "K", "dataType": "int64", "sourceColumn": "K"}],
         "partitions": [{"name": "p", "source": {"type": "calculated", "expression": "FILTER(A, A[K]>1)"}}]},
        {"name": "CG", "calculationGroup": {"calculationItems": [{"name": "YTD", "expression": "CALCULATE(SELECTEDMEASURE())"}]},
         "columns": [{"name": "Name", "dataType": "string", "sourceColumn": "Name"}]}],
        "relationships": [
            {"name": "r", "fromTable": "A", "fromColumn": "K", "toTable": "B", "toColumn": "K",
             "fromCardinality": "one", "toCardinality": "one"},
            {"name": "r2", "fromTable": "A", "fromColumn": "K", "toTable": "Missing", "toColumn": "K"}],
        "roles": [{"name": "R", "tablePermissions": [{"name": "A", "filterExpression": "A[K]=1"}]}],
        "perspectives": [{"name": "P"}]}
    osi, ledger = _convert(tmsl)
    assert osi.relationships[0].cardinality == OSICardinality.ONE_TO_ONE
    assert osi.get_dataset("B").dataset_kind == "calculated_table"
    assert osi.get_dataset("CG") is None
    c = osi.get_dataset("A").get_column("C")
    assert c.is_calculated and c.folder == "F" and c.sort_by_column == "K"
    kinds = {(r.entity_kind, r.entity_name) for r in ledger.records}
    assert {("table", "B"), ("table", "CG"), ("relationship", "A[K] -> Missing[K]"),
            ("security_role", "R"), ("perspective", "P"), ("kpi", "m"), ("hierarchy", "H"),
            ("column_property", "C sortByColumn")} <= kinds


# ---------------------------------------------------------------------------
# Defect 7: model-wide metric de-dup + column identifier collisions
# ---------------------------------------------------------------------------

def test_duplicate_measure_names_across_tables_made_unique_and_ledgered():
    tables = [
        {"name": "Fact", "columns": [_col("Amount", dataType="double")],
         "measures": [{"name": "Total Sales", "expression": "SUM(Fact[Amount])"}]},
        {"name": "Budget", "columns": [_col("Amount", dataType="double")],
         "measures": [{"name": "Total Sales", "expression": "SUM(Budget[Amount])"},
                      {"name": "Total_Sales", "expression": "1"}]},
    ]
    osi, ledger = _convert({"name": "M", "tables": tables})
    names = [m.unique_name for m in osi.metrics]
    # 'Total_Sales (Budget)' would sanitize to the same identifier as
    # 'Total Sales (Budget)', so it is suffixed further.
    assert names == ["Total Sales", "Total Sales (Budget)", "Total_Sales (Budget) 2"]
    assert len(set(n.casefold() for n in names)) == 3
    renamed = osi.metrics[1]
    assert renamed.original_name == "Total Sales" and renamed.label == "Total Sales"
    recs = [r for r in _records(ledger, "metric") if r.reason.startswith("renamed to")]
    assert {r.entity_name for r in recs} == {"Total Sales", "Total_Sales"}
    assert all(r.by_design and r.dataset == "Budget" for r in recs)
    assert recs[0].reason.startswith("renamed to 'Total Sales (Budget)' — metric name ")
    # deterministic
    again, _ = _convert({"name": "M", "tables": tables})
    assert [m.unique_name for m in again.metrics] == names


def test_within_table_duplicate_measure_rename_is_ledgered():
    tables = [{"name": "Fact", "columns": [_col("Amount", dataType="double")],
               "measures": [{"name": "X", "expression": "1"}, {"name": "X", "expression": "2"}]}]
    osi, ledger = _convert({"name": "M", "tables": tables})
    assert [m.unique_name for m in osi.metrics] == ["X_1", "X_2"]
    assert len([r for r in _records(ledger, "metric") if "is defined 2 times" in r.reason]) == 2


def test_osi_to_sml_disambiguates_columns_colliding_after_sanitization():
    ds = OSIDataset(unique_name="T", columns=[
        OSIColumn(unique_name="Sales Amount"), OSIColumn(unique_name="Sales_Amount"),
        OSIColumn(unique_name="sales amount"), OSIColumn(unique_name="Key")])
    other = OSIDataset(unique_name="U", columns=[OSIColumn(unique_name="Key")])
    dim = OSIDimension(unique_name="T", dataset="T", attributes=[
        OSIAttribute(unique_name="Sales_Amount", dataset="T", source_column="Sales_Amount")])
    rel = OSIRelationship(unique_name="REL_U_KEY__T_KEY", from_dataset="U", from_columns=["Key"],
                          to_dataset="T", to_columns=["Key"])
    osi = OSIModel(unique_name="M", datasets=[ds, other], dimensions=[dim], relationships=[rel])
    ledger = DropLedger()
    sml = OSIToSMLConverter(drop_ledger=ledger).from_osi(osi)
    t = next(d for d in sml.datasets if d.unique_name == "T")
    assert [c.unique_name for c in t.columns] == ["Sales Amount", "Sales_Amount_2", "sales amount_3", "Key"]
    assert [c.label for c in t.columns][:3] == ["Sales Amount", "Sales_Amount", "sales amount"]
    sml_dim = next(d for d in sml.dimensions if d.unique_name == "T")
    assert sml_dim.attributes[0].dataset_column == "Sales_Amount_2"
    recs = _records(ledger, "column")
    assert {r.entity_name for r in recs} == {"Sales_Amount", "sales amount"}
    assert all(r.by_design and r.dataset == "T" for r in recs)
    assert recs[0].reason.startswith("renamed to 'Sales_Amount_2' — column name collides with 'Sales Amount'")
