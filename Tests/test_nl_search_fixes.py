"""Regression tests for NL search / Ask AI fixes (D2, D6, D8, D11, D12).

All fixtures are synthetic and generic; nothing here depends on a specific
customer model.
"""

from __future__ import annotations

import pytest

from semabridge.ai.nl_query_engine import NLQueryEngine
from semabridge.ai.semantic_search import SemanticSearchIndex
from semabridge.converter.semantic_view_to_osi import SemanticViewToOSIConverter
from semabridge.intermediate.models import OSIAggregationType
from semabridge.ontology.federated_loader import FederatedOntologyLoader
from semabridge.ontology.models import (
    DomainAttribute as A,
    DomainConcept,
    DomainMetric,
    DomainOntology,
    DomainRelationship,
)

SV_DDL = '''CREATE OR REPLACE SEMANTIC VIEW DB.SC.SUPPORT_SV
TABLES ( fc AS DB.SC.FACT_CASES PRIMARY KEY (CASE_ID), da AS DB.SC.DIM_AGENT PRIMARY KEY (AGENT_ID) )
DIMENSIONS ( fc.CASE_ID AS fc.CASE_ID, da.AGENT_NAME AS "Agent Full Name" )
METRICS ( fc.CASES_MOMPCT AS COUNT(fc.CASE_ID), fc.CASE_COUNT AS COUNT(fc.CASE_ID),
          fc.TOTAL_AMT AS SUM(fc.AMT), fc.AVG_AMT AS AVG(fc.AMT), fc.MAX_AMT AS MAX(fc.AMT),
          fc.DISTINCT_AGENTS AS COUNT(DISTINCT fc.AGENT_ID),
          fc.RATIO AS SUM(fc.AMT) / COUNT(fc.CASE_ID), fc.LABELLED AS "Nice Label" )
'''


# ---------------------------------------------------------------------------
# D2: no synthetic title-cased labels / synonyms from the semantic-view importer
# ---------------------------------------------------------------------------

def _sv_osi():
    return SemanticViewToOSIConverter().to_osi({"ddl": SV_DDL})


def test_d2_semantic_view_import_keeps_raw_names_not_title_case():
    osi = _sv_osi()
    assert osi.label == "SUPPORT_SV"
    assert {d.label for d in osi.datasets} == {"FACT_CASES", "DIM_AGENT"}
    for d in osi.datasets:
        for c in d.columns:
            assert c.label == c.unique_name
    dom = FederatedOntologyLoader.from_osi_model(osi, domain_name="Support")
    for c in dom.concepts:
        # No synthesised "Fact Cases" / "Dim Agent" synonym.
        assert all(s.casefold() != c.name.replace("_", " ").casefold() for s in c.synonyms), c.synonyms
        assert c.label != c.name.replace("_", " ").title()


def test_d2_explicit_label_alias_is_preserved():
    # Negative case: a real label declared in the DDL must survive verbatim.
    osi = _sv_osi()
    attrs = [a for dim in osi.dimensions for a in dim.attributes]
    labels = {a.source_column: a.label for a in attrs}
    assert labels["AGENT_NAME"] == "Agent Full Name"
    assert labels["CASE_ID"] == "CASE_ID"
    m = {x.source_column: x for x in osi.metrics}
    assert m["LABELLED"].label == "Nice Label"


def test_d2_search_display_name_falls_back_to_raw_metric_name():
    dom = DomainOntology(
        domain="X",
        concepts=[DomainConcept(name="T", attributes=[A(name="ID")])],
        metrics=[
            DomainMetric(name="CASES_MOMPCT", sql_expression="COUNT(T.ID)", concept="T"),
            DomainMetric(name="CASE_COUNT", label="Case Count", sql_expression="COUNT(T.ID)", concept="T"),
        ],
    )
    fed = FederatedOntologyLoader.merge_domains([dom])
    idx = SemanticSearchIndex()
    idx.index_ontology(fed)
    labels = {d["name"]: d["label"] for d in idx.docs.values() if d["item_type"] == "METRIC"}
    assert labels["CASES_MOMPCT"] == "CASES_MOMPCT"
    assert labels["CASE_COUNT"] == "Case Count"


# ---------------------------------------------------------------------------
# D11: aggregation derived from SQL; never defaulted to SUM
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "expr,expected",
    [
        ("COUNT(T.ID)", "COUNT"),
        ("count( distinct T.ID )", "COUNT_DISTINCT"),
        ("SUM(T.AMT)", "SUM"),
        ("(AVG(T.AMT))", "AVG"),
        ("MIN(T.D)", "MIN"),
        ("MAX(T.D)", "MAX"),
        ("COUNT(CASE WHEN T.S = 'a)b' THEN T.ID END)", "COUNT"),
        # Negative cases: not a single aggregate -> NONE (never SUM)
        ("SUM(T.A) / COUNT(T.B)", "NONE"),
        ("SUM(T.A) OVER (PARTITION BY T.C)", "NONE"),
        ("T.AMT", "NONE"),
        ("", "NONE"),
        (None, "NONE"),
        ("COUNT(T.A) - LAG(COUNT(T.A))", "NONE"),
    ],
)
def test_d11_infer_aggregation_from_sql(expr, expected):
    from semabridge.ontology.models import infer_aggregation_from_sql

    assert infer_aggregation_from_sql(expr) == expected


def test_d11_domain_metric_default_is_not_sum():
    assert DomainMetric(name="X").aggregation_type == "NONE"
    assert DomainMetric(name="X", sql_expression="COUNT(T.ID)").aggregation_type == "COUNT"
    assert DomainMetric(name="X", sql_expression="SUM(T.A)/COUNT(T.B)").aggregation_type == "NONE"
    # An explicitly stated aggregation is respected.
    assert DomainMetric(name="X", sql_expression="SUM(T.A)", aggregation_type="NON_ADDITIVE").aggregation_type == "NON_ADDITIVE"


def test_d11_semantic_view_import_derives_aggregation():
    osi = _sv_osi()
    agg = {m.source_column: m.aggregation for m in osi.metrics}
    assert agg["CASE_COUNT"] == OSIAggregationType.COUNT
    assert agg["CASES_MOMPCT"] == OSIAggregationType.COUNT
    assert agg["TOTAL_AMT"] == OSIAggregationType.SUM
    assert agg["AVG_AMT"] == OSIAggregationType.AVG
    assert agg["MAX_AMT"] == OSIAggregationType.MAX
    assert agg["DISTINCT_AGENTS"] == OSIAggregationType.COUNT_DISTINCT
    # Negative: ratio / label-only metrics are not invented as SUM.
    assert agg["RATIO"] == OSIAggregationType.NONE
    assert agg["LABELLED"] == OSIAggregationType.NONE
    dom = FederatedOntologyLoader.from_osi_model(osi, domain_name="Support")
    dm = {m.name.split(".")[-1]: m.aggregation_type for m in dom.metrics}
    assert dm["CASE_COUNT"] == "COUNT"
    assert dm["RATIO"] == "NONE"


def test_d11_ask_ai_header_reports_sql_aggregation_not_stale_sum():
    dom = DomainOntology(
        domain="X",
        concepts=[DomainConcept(name="T", attributes=[A(name="ID"), A(name="AMT", data_type="NUMBER")])],
        metrics=[
            # Legacy ontology: aggregation_type was persisted as the old SUM default.
            DomainMetric(name="ROW_COUNT", label="Row Count", sql_expression="COUNT(T.ID)", concept="T", aggregation_type="SUM"),
            DomainMetric(name="AMOUNT_TOTAL", label="Amount Total", sql_expression="SUM(T.AMT)", concept="T"),
        ],
    )
    fed = FederatedOntologyLoader.merge_domains([dom])
    idx = SemanticSearchIndex()
    idx.index_ontology(fed)
    eng = NLQueryEngine(idx, fed)
    p = eng.resolve_query("row count")
    assert "-- Metric: Row Count (Aggregation: COUNT)" in p.generated_sql
    p = eng.resolve_query("amount total")
    assert "-- Metric: Amount Total (Aggregation: SUM)" in p.generated_sql


# ---------------------------------------------------------------------------
# Shared synthetic support/sales ontology for Ask AI tests
# ---------------------------------------------------------------------------

def _support_ontology() -> DomainOntology:
    return DomainOntology(
        domain="Support",
        concepts=[
            DomainConcept(
                name="FACT_CASES",
                label="Fact Cases",
                description="Support cases fact table with status, owner, priority, channel and region",
                attributes=[
                    A(name="CASE_ID"), A(name="AGENT_NAME"), A(name="PRODUCT_NAME"),
                    A(name="CREATED_DATE", data_type="DATE"), A(name="STATUS"), A(name="REGION"),
                    A(name="SLA_HOURS", data_type="NUMBER"),
                ],
            ),
            DomainConcept(name="DIM_PRODUCT", label="Product", attributes=[A(name="PRODUCT_ID"), A(name="PRODUCT_NAME")]),
            DomainConcept(name="DIM_AGENT", label="Agent", attributes=[A(name="AGENT_ID"), A(name="AGENT_NAME")]),
            DomainConcept(name="DIM_DATE", attributes=[A(name="DATE", data_type="DATE"), A(name="MONTH_NAME")]),
            DomainConcept(name="FACT_OPPS", label="Opportunities", attributes=[
                A(name="OPP_ID"), A(name="AMOUNT", data_type="NUMBER"), A(name="STAGE"), A(name="CLOSE_DATE", data_type="DATE"),
            ]),
        ],
        metrics=[
            DomainMetric(name="CASES", label="Cases", sql_expression="COUNT(FACT_CASES.CASE_ID)", concept="FACT_CASES"),
            DomainMetric(name="CASES_MOMPCT", label="Cases Mompct",
                         sql_expression="(COUNT(CASE_ID)-LAG(COUNT(CASE_ID)))/LAG(COUNT(CASE_ID))", concept="FACT_CASES"),
            DomainMetric(name="OPPORTUNITY_COUNT", label="Opportunity Count", sql_expression="COUNT(FACT_OPPS.OPP_ID)", concept="FACT_OPPS"),
            DomainMetric(name="WON_OPPORTUNITIES", label="Won Opportunities",
                         sql_expression="COUNT(CASE WHEN STAGE='Won' THEN OPP_ID END)", concept="FACT_OPPS"),
            DomainMetric(name="LOST_OPPORTUNITIES", label="Lost Opportunities",
                         sql_expression="COUNT(CASE WHEN STAGE='Lost' THEN OPP_ID END)", concept="FACT_OPPS"),
            DomainMetric(name="TOTAL_REVENUE", label="Total Revenue", description="Sum of opportunity amount",
                         sql_expression="SUM(FACT_OPPS.AMOUNT)", concept="FACT_OPPS"),
            DomainMetric(name="AVG_DEAL", label="Avg Deal", description="Average of opportunity amount",
                         sql_expression="AVG(FACT_OPPS.AMOUNT)", concept="FACT_OPPS"),
        ],
        relationships=[
            DomainRelationship(name="r1", from_concept="FACT_CASES", to_concept="DIM_PRODUCT",
                               join_expression="FACT_CASES.PRODUCT_NAME = DIM_PRODUCT.PRODUCT_NAME"),
            DomainRelationship(name="r2", from_concept="FACT_CASES", to_concept="DIM_AGENT",
                               join_expression="FACT_CASES.AGENT_NAME = DIM_AGENT.AGENT_NAME"),
            DomainRelationship(name="r3", from_concept="FACT_CASES", to_concept="DIM_DATE",
                               join_expression="FACT_CASES.CREATED_DATE = DIM_DATE.DATE"),
        ],
    )


@pytest.fixture(scope="module")
def engine() -> NLQueryEngine:
    fed = FederatedOntologyLoader.merge_domains([_support_ontology()])
    idx = SemanticSearchIndex()
    idx.index_ontology(fed)
    return NLQueryEngine(idx, fed)


# ---------------------------------------------------------------------------
# D12: "number of <X>" is count syntax; SV dimension naming matches the emitter
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "question,metric,dims",
    [
        ("number of cases", "Cases", []),
        ("total number of cases by agent", "Cases", ["AGENT_NAME"]),
        ("number of won opportunities", "Won Opportunities", []),
        ("number of opportunities", "Opportunity Count", []),
        ("count of cases", "Cases", []),
        ("how many cases", "Cases", []),
    ],
)
def test_d12_number_of_resolves_count_metric(engine, question, metric, dims):
    p = engine.resolve_query(question)
    assert p.answerable, p.warnings
    assert p.detected_metrics == [metric]
    assert p.detected_dimensions == dims


@pytest.mark.parametrize("question", ["number of weather stations", "number of agents", "number of products"])
def test_d12_number_of_unrelated_still_refuses(engine, question):
    p = engine.resolve_query(question)
    assert not p.answerable
    assert p.detected_metrics == []


def test_d12_semantic_view_date_ref_matches_emitter_naming(engine):
    from semabridge.connectors.semantic_ddl_sanitizer import SemanticDDLSanitizer
    from semabridge.utils.identifiers import IdentifierSanitizer

    emitter_names = SemanticDDLSanitizer(IdentifierSanitizer())
    expected = f"{IdentifierSanitizer().to_alias('DIM_DATE')}.{emitter_names.sanitize_semantic_name('DATE')}"
    assert expected == "DIM_DATE.COL_DATE"
    p = engine.resolve_query("cases in 2024", semantic_view="DB.SC.SUPPORT_SV")
    assert f"YEAR({expected}) = 2024" in p.generated_sql


def test_d12_sv_dim_ref_prefers_deployed_alias():
    from types import SimpleNamespace

    c = DomainConcept(name="DIM_DATE", attributes=[A(name="DATE", data_type="DATE")])
    a = c.attributes[0]
    other = DomainConcept(name="DIM_X", attributes=[A(name="DATE", data_type="DATE")])
    deployed = SimpleNamespace(dimensions=[
        SimpleNamespace(concept=other, attribute=other.attributes[0], sv_dim_alias=("WRONG", "WRONG")),
        SimpleNamespace(concept=c, attribute=a, sv_dim_alias=("DD", "CALENDAR_DATE")),
    ])
    assert NLQueryEngine._sv_dim_ref(c, a, deployed) == "DD.CALENDAR_DATE"
    # No deploy metadata -> emitter-consistent sanitised name.
    assert NLQueryEngine._sv_dim_ref(c, a, SimpleNamespace(dimensions=[])) == "DIM_DATE.COL_DATE"


# ---------------------------------------------------------------------------
# D8: aggregation/description-aware matching; unapplied time intent is warned
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "question,metric",
    [
        ("sum of amount", "Total Revenue"),
        ("total amount", "Total Revenue"),
        ("what is the sum of opportunity amount", "Total Revenue"),
        ("average amount", "Avg Deal"),
        ("average of opportunity amount", "Avg Deal"),
    ],
)
def test_d8_aggregation_aware_matching_picks_described_metric(engine, question, metric):
    p = engine.resolve_query(question)
    assert p.answerable, p.warnings
    assert p.detected_metrics == [metric]


@pytest.mark.parametrize("question", ["max amount", "sum of sla hours", "count of amount", "weather in paris", "amount"])
def test_d8_aggregation_aware_matching_does_not_pick_wrong_metric(engine, question):
    # No metric computes MAX/SUM over these columns: refuse instead of guessing.
    p = engine.resolve_query(question)
    assert not p.answerable, (question, p.detected_metrics)


def test_d8_trend_over_time_resolves_with_warning(engine):
    p = engine.resolve_query("cases trend over time")
    assert p.answerable
    assert p.detected_metrics == ["Cases"]
    assert any("'trend over time' was not applied" in w for w in p.warnings), p.warnings


def test_d8_growth_and_relative_period_are_warned_not_dropped(engine):
    p = engine.resolve_query("revenue growth last quarter")
    assert p.detected_metrics == ["Total Revenue"]
    assert "WHERE" not in p.generated_sql
    assert any("'growth' was not applied" in w for w in p.warnings), p.warnings
    assert any("'last quarter' was not applied" in w for w in p.warnings), p.warnings
    assert p.parsed_phrases["time_intent"] == ["growth", "last quarter"]


def test_d8_no_time_warning_when_nothing_was_asked_or_it_is_applied(engine):
    for q in ("total revenue", "cases by agent", "cases in 2024"):
        p = engine.resolve_query(q)
        assert p.answerable
        assert not [w for w in p.warnings if "was not applied" in w], (q, p.warnings)


def test_d8_time_intent_counted_as_applied_when_metric_expresses_it():
    dom = _support_ontology()
    dom.metrics.append(
        DomainMetric(name="REVENUE_GROWTH", label="Revenue Growth", sql_expression="SUM(FACT_OPPS.AMOUNT)", concept="FACT_OPPS")
    )
    fed = FederatedOntologyLoader.merge_domains([dom])
    idx = SemanticSearchIndex()
    idx.index_ontology(fed)
    p = NLQueryEngine(idx, fed).resolve_query("revenue growth")
    assert p.detected_metrics == ["Revenue Growth"]
    assert not [w for w in p.warnings if "was not applied" in w], p.warnings


# ---------------------------------------------------------------------------
# D6: physical-table SQL only as an explicitly requested, labelled draft
# ---------------------------------------------------------------------------

def test_d6_no_physical_sql_when_view_unknown_and_draft_not_requested(engine):
    for dialect in ("SNOWFLAKE", "DATABRICKS"):
        p = engine.resolve_query("cases by agent", dialect=dialect, offline_draft=False)
        assert not p.answerable
        assert not p.is_draft
        assert "SELECT" not in p.generated_sql
        assert "FROM FACT_CASES" not in p.generated_sql
        assert any("No deployed semantic view" in w for w in p.warnings), p.warnings


def test_d6_explicit_offline_draft_is_labelled(engine):
    p = engine.resolve_query("cases by agent", dialect="SNOWFLAKE", offline_draft=True)
    assert p.answerable and p.is_draft
    assert "FROM FACT_CASES" in p.generated_sql
    assert p.generated_sql.startswith("-- SemaBridge AI OFFLINE DRAFT")
    assert "Governed Semantic Query" not in p.generated_sql
    assert "Cortex Analyst" not in p.generated_sql
    assert any(w.startswith("OFFLINE DRAFT") for w in p.warnings)


def test_d6_local_dialect_is_a_draft_even_if_draft_flag_false(engine):
    p = engine.resolve_query("cases by agent", dialect="LOCAL", offline_draft=False)
    assert p.answerable and p.is_draft
    assert "OFFLINE DRAFT" in p.generated_sql


def test_d6_unspecified_flag_still_labels_physical_sql_as_draft(engine):
    p = engine.resolve_query("cases by agent")
    assert p.answerable and p.is_draft
    assert "OFFLINE DRAFT" in p.generated_sql


def test_d6_semantic_view_query_is_governed_not_draft(engine):
    # Negative: a known semantic view never yields a draft, whatever the flag.
    for flag in (False, True, None):
        p = engine.resolve_query("cases by agent", semantic_view="DB.SC.SUPPORT_SV", offline_draft=flag)
        assert p.answerable and not p.is_draft
        assert "FROM SEMANTIC_VIEW(DB.SC.SUPPORT_SV" in p.generated_sql
        assert "OFFLINE DRAFT" not in p.generated_sql
        assert not any(w.startswith("OFFLINE DRAFT") for w in p.warnings)


def test_d6_semantic_view_from_metric_metadata_is_used_without_draft():
    dom = _support_ontology()
    dom.metrics[0] = dom.metrics[0].model_copy(update={"semantic_view_name": "DB.SC.SUPPORT_SV"})
    fed = FederatedOntologyLoader.merge_domains([dom])
    idx = SemanticSearchIndex()
    idx.index_ontology(fed)
    p = NLQueryEngine(idx, fed).resolve_query("cases", dialect="SNOWFLAKE", offline_draft=False)
    assert p.answerable and not p.is_draft
    assert "SEMANTIC_VIEW(DB.SC.SUPPORT_SV" in p.generated_sql
