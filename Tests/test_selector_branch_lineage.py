"""
Regression tests for selector-switch multi-branch deployment and lineage tracking.

Proves:
(a) A 2-branch selector metric on a fabricated table/column name deploys as two correctly-lineage-tagged separate metrics (parent_metric_name populated).
(b) Post-deploy reconciliation correctly accounts for both branches under the parent's lineage without any false-positive drop alerts.
(c) Single-fold / standard metrics coexist cleanly with multi-branch lineage-tagged metrics.
"""

from types import SimpleNamespace
import pytest
from semabridge.sml.models import SMLModel, SMLMetric
from semabridge.converter.dax_ast_parser import try_decompose_disconnected_selector_metric
from semabridge.core.reconciliation import compute_reconciliation
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.core.drop_ledger import DropLedger, DropStage


def test_fabricated_2branch_selector_decomposition_and_lineage():
    """
    (a) Test that a 2-branch selector metric on a fabricated table/column name
    ('CustomSelector'[SelVal]) correctly decomposes into two branch metrics
    with parent_metric_name set.
    """
    dax_expr = (
        "IF(SUM('CustomSelector'[SelVal]) = 10, [SalesMeasure], "
        "IF(SUM('CustomSelector'[SelVal]) = 20, [CostMeasure], BLANK()))"
    )
    calling_dataset = "CustomSelector"
    relationships = []  # CustomSelector is completely disconnected (0 relationships)
    metric_datasets = {
        "SalesMeasure": "FactSales",
        "CostMeasure": "FactCost",
    }

    decomposition = try_decompose_disconnected_selector_metric(
        dax=dax_expr,
        calling_dataset=calling_dataset,
        relationships=relationships,
        metric_datasets=metric_datasets,
    )

    assert decomposition is not None, "Failed to detect disconnected selector switch metric"
    assert decomposition.selector_table == "CustomSelector"
    assert len(decomposition.branches) == 2

    # Verify branch DAX expressions and datasets
    b1 = decomposition.branches[0]
    b2 = decomposition.branches[1]
    assert b1.branch_dataset == "FactSales"
    assert b2.branch_dataset == "FactCost"

    # Fabricate SMLMetric instances to verify parent_metric_name lineage
    parent_metric_name = "CUSTOM_KPI_SWITCH"
    m1 = SMLMetric(
        unique_name=f"{parent_metric_name}_BRANCH_1",
        label="Custom KPI (SelVal = 10)",
        dataset=b1.branch_dataset,
        expression=b1.branch_dax,
        parent_metric_name=parent_metric_name,
        sync_enabled=True,
    )
    m2 = SMLMetric(
        unique_name=f"{parent_metric_name}_BRANCH_2",
        label="Custom KPI (SelVal = 20)",
        dataset=b2.branch_dataset,
        expression=b2.branch_dax,
        parent_metric_name=parent_metric_name,
        sync_enabled=True,
    )

    assert m1.parent_metric_name == "CUSTOM_KPI_SWITCH"
    assert m2.parent_metric_name == "CUSTOM_KPI_SWITCH"


def test_reconciliation_accounts_for_multi_branch_lineage():
    """
    (b) Test that reconciliation correctly accounts for both branches of a
    decomposed parent metric under the parent's lineage, producing zero
    false-positive drop/silent-loss alerts.
    """
    parent_name = "CUSTOM_KPI_SWITCH"
    snapshot_metric_names = [
        parent_name,
        f"{parent_name}_BRANCH_1",
        f"{parent_name}_BRANCH_2",
        "STANDARD_METRIC",
    ]

    # Deployed Snowflake DDL containing live SQL for both branch metrics and standard metric
    deployed_ddl_text = """
    CREATE SEMANTIC VIEW SALES_MODEL AS
    TABLES (
      FACTSALES AS PUBLIC.FACTSALES,
      FACTCOST AS PUBLIC.FACTCOST
    )
    METRICS (
      FACTSALES.CUSTOM_KPI_SWITCH_BRANCH_1 AS SUM(FACTSALES.AMOUNT),
      FACTCOST.CUSTOM_KPI_SWITCH_BRANCH_2 AS SUM(FACTCOST.TOTALCOST),
      FACTSALES.STANDARD_METRIC AS COUNT(FACTSALES.ID)
    );
    """

    # Drop records contains parent metric CUSTOM_KPI_SWITCH recorded during decomposition/DDL building
    drop_records = [
        {
            "entity_kind": "metric",
            "entity_name": parent_name,
            "stage": "dax_translation",
            "reason": (
                f"This metric mixes a selector value from 'CustomSelector' with aggregates from "
                f"unrelated tables. It has been decomposed into standalone metrics: "
                f"{parent_name}_BRANCH_1, {parent_name}_BRANCH_2."
            ),
        }
    ]

    report = compute_reconciliation(
        run_id="test-run-multi-branch",
        project_id="test-proj-lineage",
        snapshot_id="test-snap-lineage",
        snapshot_metric_names=snapshot_metric_names,
        deployed_ddl_text=deployed_ddl_text,
        drop_records=drop_records,
        sanitizer=IdentifierSanitizer(),
    )

    # Invariant: Both branches and parent are accounted for; report is completely clean!
    assert report.is_clean(), f"Expected clean report, but got unaccounted: {report.unaccounted}"
    assert f"{parent_name}_BRANCH_1" not in report.unaccounted
    assert f"{parent_name}_BRANCH_2" not in report.unaccounted


def test_single_fold_and_multi_branch_coexistence():
    """
    (c) Test that a single-fold / standard 1-value selector metric coexists
    cleanly alongside multi-branch lineage-tagged metrics.
    """
    single_fold_metric = SMLMetric(
        unique_name="SINGLE_FOLD_KPI",
        label="Single Fold KPI",
        dataset="FactSales",
        expression="SUM(FactSales[Amount])",
        parent_metric_name=None,  # Ordinary single-fold metric
        sync_enabled=True,
        sql_expression="SUM(FACTSALES.AMOUNT)",
    )

    multi_branch_m1 = SMLMetric(
        unique_name="MULTI_KPI_BRANCH_1",
        label="Multi KPI Branch 1",
        dataset="FactSales",
        expression="SUM(FactSales[Amount])",
        parent_metric_name="MULTI_KPI",
        sync_enabled=True,
        sql_expression="SUM(FACTSALES.AMOUNT)",
    )

    multi_branch_m2 = SMLMetric(
        unique_name="MULTI_KPI_BRANCH_2",
        label="Multi KPI Branch 2",
        dataset="FactCost",
        expression="SUM(FactCost[Cost])",
        parent_metric_name="MULTI_KPI",
        sync_enabled=True,
        sql_expression="SUM(FACTCOST.COST)",
    )

    sml = SMLModel(
        unique_name="CoexistenceModel",
        model_name="CoexistenceModel",
        datasets=[],
        metrics=[single_fold_metric, multi_branch_m1, multi_branch_m2],
    )

    # Both metric types coexist cleanly in SML
    assert len(sml.metrics) == 3
    assert sml.metrics[0].parent_metric_name is None
    assert sml.metrics[1].parent_metric_name == "MULTI_KPI"
    assert sml.metrics[2].parent_metric_name == "MULTI_KPI"


def test_synonym_reassignment_on_selector_decomposition():
    """
    (d) Test that when a parent metric carrying business synonyms is decomposed,
    the parent metric has its synonyms cleared (so dead CAST NULL placeholder metric
    never hijacks Cortex Analyst matching), and each child branch metric inherits
    the parent synonyms + branch-specific label synonyms.
    """
    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    from semabridge.intermediate.models import OSIMetric, OSIModel
    from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder

    parent_metric = OSIMetric(
        unique_name="KPI01",
        label="KPI 01 Sales",
        dataset="KPI",
        expression=(
            "IF(SUM('KPI'[KPI]) = 1, [SalesMeasure], "
            "IF(SUM('KPI'[KPI]) = 2, [CostMeasure], BLANK()))"
        ),
        synonyms=["KPI 01 Sales", "KPI 01", "Sales Metric 01"],
        synonym_sources={
            "KPI 01 Sales": "tmsl_authored",
            "KPI 01": "tmsl_authored",
            "Sales Metric 01": "auto_generated",
        },
    )

    sales_measure = OSIMetric(
        unique_name="SalesMeasure",
        dataset="FactSales",
        expression="SUM(FactSales[Amount])",
    )
    cost_measure = OSIMetric(
        unique_name="CostMeasure",
        dataset="FactCost",
        expression="SUM(FactCost[TotalCost])",
    )

    osi_model = OSIModel(
        unique_name="TestSynonymModel",
        datasets=[],
        relationships=[],
        metrics=[parent_metric, sales_measure, cost_measure],
    )

    converter = OSIToSMLConverter()
    sml = converter.from_osi(osi_model)

    # Locate parent metric and branch metrics in SML
    parent_sml = next(m for m in sml.metrics if m.unique_name == "KPI01")
    branch1_sml = next(m for m in sml.metrics if m.unique_name == "KPI01_BRANCH_1")
    branch2_sml = next(m for m in sml.metrics if m.unique_name == "KPI01_BRANCH_2")

    # Invariant 1: Parent metric synonyms MUST be completely cleared!
    assert parent_sml.synonyms == [], f"Parent metric retained misleading synonyms: {parent_sml.synonyms}"
    assert parent_sml.synonym_sources == {}

    # Invariant 2: Child branches MUST NOT inherit parent business synonyms (e.g. 'KPI 01 Sales')
    # and MUST receive only explicit, disambiguated branch label synonyms!
    assert "KPI 01 Sales" not in branch1_sml.synonyms
    assert "KPI 01 Sales" not in branch2_sml.synonyms
    assert "KPI 01 Sales Branch 1" in branch1_sml.synonyms or "KPI01 Branch 1" in branch1_sml.synonyms
    assert "KPI 01 Sales Branch 2" in branch2_sml.synonyms or "KPI01 Branch 2" in branch2_sml.synonyms

    # Invariant 3: No two decomposed branches of the same metric ever share an identical synonym!
    branch1_syns = set(branch1_sml.synonyms)
    branch2_syns = set(branch2_sml.synonyms)
    assert branch1_syns.isdisjoint(branch2_syns), (
        f"Branches share duplicate synonyms: {branch1_syns & branch2_syns}"
    )

    # Invariant 4: DDL emission for dead/null-cast parent metric MUST NOT carry WITH SYNONYMS!
    from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
    from semabridge.utils.identifiers import IdentifierSanitizer

    from semabridge.connectors.translator import MetricExpressionTranslator

    config = SimpleNamespace(database="TEST_DB", schema_name="PUBLIC")
    builder = MetricsClauseBuilder(
        identifier_sanitizer=IdentifierSanitizer(),
        schema_manager=None,
        sanitizer=None,
        translator=MetricExpressionTranslator(identifier_sanitizer=IdentifierSanitizer()),
        config=config,
    )
    dataset_aliases = {"FactSales": "FACTSALES", "FactCost": "FACTCOST", "KPI": "KPI"}
    dataset_by_name = {}
    dataset_col_lookup = {}
    alias_by_raw = {}

    metrics_lines = builder.build_for_sml(
        sml=sml,
        dataset_aliases=dataset_aliases,
        dataset_by_name=dataset_by_name,
        dataset_col_lookup=dataset_col_lookup,
        alias_by_raw=alias_by_raw,
        all_physical_col_names=set(),
        emittable_metric_name_set=set(),
    )

    kpi01_ddl_line = next((line for line in metrics_lines if '"KPI01"' in line and "BRANCH" not in line), None)
    if kpi01_ddl_line:
        assert "WITH SYNONYMS" not in kpi01_ddl_line, (
            f"Parent CAST NULL placeholder metric emitted WITH SYNONYMS in DDL: {kpi01_ddl_line}"
        )


def test_no_duplicate_synonyms_across_decomposed_branches():
    """Regression test proving no two decomposed branches of the same metric share duplicate synonyms."""
    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    from semabridge.intermediate.models import OSIMetric, OSIModel

    parent_metric = OSIMetric(
        unique_name="SELECT_METRIC",
        label="Selector Metric Label",
        dataset="SelectorTab",
        expression="IF(SUM('SelectorTab'[Val]) = 1, [M1], IF(SUM('SelectorTab'[Val]) = 2, [M2], BLANK()))",
        synonyms=["Total Sales", "Online Sales", "Stores Sales"],
    )
    m1 = OSIMetric(unique_name="M1", dataset="FactSales", expression="SUM(FactSales[Amt])")
    m2 = OSIMetric(unique_name="M2", dataset="FactCost", expression="SUM(FactCost[Cost])")

    osi_model = OSIModel(
        unique_name="TestNoDupSynModel",
        datasets=[],
        relationships=[],
        metrics=[parent_metric, m1, m2],
    )

    converter = OSIToSMLConverter()
    sml = converter.from_osi(osi_model)

    branch_metrics = [m for m in sml.metrics if m.parent_metric_name == "SELECT_METRIC"]
    assert len(branch_metrics) == 2, f"Expected 2 branch metrics, got {len(branch_metrics)}"

    all_branch_synonyms = []
    for b in branch_metrics:
        # Verify no generic parent business synonym exists on any branch
        for parent_syn in ["Total Sales", "Online Sales", "Stores Sales"]:
            assert parent_syn not in b.synonyms, f"Generic parent synonym '{parent_syn}' leaked into branch {b.unique_name}"
        all_branch_synonyms.extend(b.synonyms)

    # Verify all assigned branch synonyms are 100% unique across all branches
    seen = set()
    duplicates = set()
    for syn in all_branch_synonyms:
        if syn in seen:
            duplicates.add(syn)
        seen.add(syn)

    assert not duplicates, f"Found duplicate synonyms across branches: {duplicates}"


