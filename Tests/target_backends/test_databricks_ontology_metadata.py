"""V1: ontology meaning (descriptions, synonyms) edited by people reaches the
Databricks metric-view YAML, through the same path a run takes:
step 6-ontology (apply to the model) -> step 6b (planner) -> writer."""

from __future__ import annotations

import yaml

from semabridge.ontology.apply import apply_ontology_to_model
from semabridge.ontology.models import DomainAttribute, DomainConcept, DomainMetric, DomainOntology
from semabridge.sml.models import Cardinality, CrossFilterDirection, SMLColumn, SMLDataset, SMLMetric, SMLModel, SMLRelationship
from semabridge.target_backends.databricks.planner import PlannerSettings, plan_metric_views
from semabridge.target_backends.databricks.tables import plan_tables
from semabridge.target_backends.databricks.writer import render_plan

OUT = ("workspace", "semabridge_dev")


def _model() -> SMLModel:
    def col(name, desc=""):
        return SMLColumn(unique_name=name, label=name, description=desc)

    return SMLModel(
        unique_name="m", label="Chain Model",
        datasets=[
            SMLDataset(unique_name="Sales", label="Sales", is_fact=True,
                       columns=[col("SaleID"), col("StoreID"), col("Amount")]),
            SMLDataset(unique_name="Store", label="Store", columns=[col("StoreID"), col("Store Name")]),
        ],
        relationships=[SMLRelationship(unique_name="s_st", from_dataset="Sales", from_columns=["StoreID"],
                                       to_dataset="Store", to_columns=["StoreID"],
                                       cardinality=Cardinality("many-to-one"), is_active=True,
                                       cross_filter=CrossFilterDirection.SINGLE)],
        metrics=[SMLMetric(unique_name="Total Sales", label="Total Sales", dataset="Sales",
                           expression="SUM(Sales[Amount])", sql_expression="SUM(SALES.`AMOUNT`::DOUBLE)",
                           sync_enabled=True)],
    )


def _domain() -> DomainOntology:
    return DomainOntology(
        domain="Chain Model", id="chain_model",
        concepts=[DomainConcept(name="Store", attributes=[
            DomainAttribute(name="Store Name", description="Trading name shown on receipts",
                            synonyms=["shop name"])])],
        metrics=[DomainMetric(name="Total Sales", description="All sales, curated by finance",
                              synonyms=["revenue total"])],
    )


def _docs(model) -> dict:
    tp = plan_tables(model, {OUT: {}}, source_schemas=[OUT], on_missing_source="stub")
    s = PlannerSettings(security_roles=0, output_catalog=OUT[0], output_schema=OUT[1], existing_objects=frozenset())
    plan = plan_metric_views(model, tp, s).as_dict()
    plan["meta"] = {"revision": 1, "status": "valid"}
    return {rv.view: yaml.safe_load(rv.yaml)
            for rv in render_plan(plan, catalog=OUT[0], schema=OUT[1], model_label="Chain Model")}


def test_ontology_synonyms_and_descriptions_reach_the_metric_view_yaml() -> None:
    model = _model()
    report = apply_ontology_to_model(model, domains=[_domain()], model_names=["m", "Chain Model"])
    assert not report.get("unmatched"), report.get("unmatched")

    doc = _docs(model)["sales_metrics"]
    measure = next(m for m in doc["measures"] if m["name"] == "total_sales")
    assert "revenue total" in measure["synonyms"]
    assert "All sales, curated by finance" in str(measure.get("comment") or "")

    field = next(f for f in doc["fields"] if f["name"] == "store_name")
    assert "shop name" in field["synonyms"]
    assert "Trading name shown on receipts" in str(field.get("comment") or "")


def test_without_the_ontology_the_yaml_has_no_curated_metadata() -> None:
    doc = _docs(_model())["sales_metrics"]
    measure = next(m for m in doc["measures"] if m["name"] == "total_sales")
    assert "revenue total" not in (measure.get("synonyms") or [])
