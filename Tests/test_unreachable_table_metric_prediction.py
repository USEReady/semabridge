import pytest
from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
from semabridge.connectors.translator import MetricExpressionTranslator
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.core.drop_ledger import DropLedger, DropStage

class DummyConfig:
    def __init__(self, database="MOCK_DB", schema_name="MOCK_SCHEMA"):
        self.database = database
        self.schema_name = schema_name
        self.auth_type = "externalbrowser"

class DummyMetric:
    def __init__(self, unique_name, dataset, expression, sql_expression=None):
        self.unique_name = unique_name
        self.dataset = dataset
        self.expression = expression
        self.sql_expression = sql_expression or expression
        self.synonyms = []
        self.aggregation = None
        self.source_column = None
        self.sync_enabled = True

class DummyDataset:
    def __init__(self, unique_name, is_fact=False):
        self.unique_name = unique_name
        self.is_fact = is_fact

class DummyRelationship:
    def __init__(self, from_ds, to_ds, is_active=True):
        self.from_dataset = from_ds
        self.to_dataset = to_ds
        self.is_active = is_active

class DummyModel:
    def __init__(self, unique_name, datasets, metrics, relationships):
        self.unique_name = unique_name
        self.label = unique_name
        self.datasets = datasets
        self.metrics = metrics
        self.relationships = relationships

def test_unreachable_table_metric_predicted_as_drop_during_ddl_emission():
    """
    Regression Test:
    Verify that MetricsClauseBuilder static relationship reachability check:
    1. Identifies KPI01 referencing unreachable COL_DATE from anchor dataset KPI.
    2. Records KPI01 in drop_ledger during DDL_EMISSION stage (enabling Dry Run predicted failures).
    3. Emits CAST(NULL AS DOUBLE) for KPI01 while preserving valid metrics (TOTAL_UNITS_YTD).
    """
    id_sanitizer = IdentifierSanitizer()
    drop_ledger = DropLedger()
    config = DummyConfig()
    translator = MetricExpressionTranslator(config=config, identifier_sanitizer=id_sanitizer)

    builder = MetricsClauseBuilder(
        identifier_sanitizer=id_sanitizer,
        schema_manager=None,
        sanitizer=None,
        translator=translator,
        config=config,
        drop_ledger=drop_ledger,
    )

    kpi_ds = DummyDataset("KPI", is_fact=False)
    sales_ds = DummyDataset("SalesFact", is_fact=True)
    date_ds = DummyDataset("Date", is_fact=False)

    rel = DummyRelationship("SalesFact", "Date")

    m_kpi = DummyMetric(
        "KPI01",
        "KPI",
        "SUM(KPI.KPI) + COL_DATE.RUNNING_YEAR",
        "CASE WHEN SUM(KPI.KPI::FLOAT) = 1 THEN SUM(CASE WHEN COL_DATE.RUNNING_YEAR = 1 THEN SALESFACT.UNITS ELSE NULL END::FLOAT) ELSE NULL END"
    )
    m_valid = DummyMetric(
        "TOTAL_UNITS_YTD",
        "SalesFact",
        "SUM(SALESFACT.UNITS)",
        "SUM(CASE WHEN SALESFACT.IS_YTD THEN SALESFACT.UNITS ELSE NULL END::FLOAT)"
    )

    model = DummyModel(
        "MOCK_MODEL",
        [kpi_ds, sales_ds, date_ds],
        [m_kpi, m_valid],
        [rel]
    )

    dataset_aliases = {"KPI": "KPI", "SalesFact": "SALESFACT", "Date": "COL_DATE"}
    dataset_by_name = {"KPI": kpi_ds, "SalesFact": sales_ds, "Date": date_ds}
    dataset_col_lookup = {"KPI": {"KPI", "CATEGORY"}, "SalesFact": {"UNITS", "IS_YTD"}, "Date": {"RUNNING_YEAR"}}

    emittable_set = set()
    lines = builder._build_metrics(
        model=model,
        dataset_aliases=dataset_aliases,
        dataset_by_name=dataset_by_name,
        dataset_col_lookup=dataset_col_lookup,
        alias_by_raw={},
        all_physical_col_names={"KPI", "CATEGORY", "UNITS", "IS_YTD", "RUNNING_YEAR"},
        emittable_metric_name_set=emittable_set,
        is_osi=False,
    )

    # 1. Verify drop_ledger recorded KPI01 at DDL_EMISSION stage
    recorded_drops = drop_ledger.to_json()
    kpi_drop = next((d for d in recorded_drops if d.get("entity_name") == "KPI01"), None)
    assert kpi_drop is not None, "Expected KPI01 to be recorded in drop_ledger during DDL_EMISSION"
    assert kpi_drop["stage"] == DropStage.DDL_EMISSION.value
    assert "reachable" in kpi_drop["reason"].lower()

    # 2. Verify DDL emitted CAST(NULL AS DOUBLE) for KPI01 and valid expression for TOTAL_UNITS_YTD
    ddl_text = "\n".join(lines)
    assert '"KPI01" AS CAST(NULL AS DOUBLE)' in ddl_text
    assert 'SALESFACT."TOTAL_UNITS_YTD" AS SUM(CASE WHEN SALESFACT.IS_YTD THEN SALESFACT.UNITS ELSE NULL END::FLOAT)' in ddl_text
