"""End-to-end reproduction of the reported SALES_RETURNS_SAMPLE incident:

    SELECT SUM(sales.unit) AS total_unit_quantity
    FROM SEMANTIC_VIEW(...) METRICS SUM(sales.unit)
    -> SQL compilation error: invalid identifier 'SALES.UNIT'

Root cause: SALES has two columns that both sanitize to "UNIT" (a genuine
same-table collision), so schema_manager's physical-column dedup suffixed
them into UNIT_1/UNIT_2 -- but a direct-aggregation metric whose
source_column is the raw, un-suffixed "unit" could never resolve to its
own suffixed sibling once the table was actually deployed (live schema
confirmed), because duplicate-sibling resolution was gated off entirely
for that case. It fell through to a naive re-sanitized "UNIT" -- a name
that was never a real physical column -- producing exactly this error.

This test wires a REAL SnowflakeSchemaManager (not a fake) together with
MetricsClauseBuilder, in the exact live-schema-confirmed shape the real
incident hit, and confirms the emitted METRICS clause now references the
correct suffixed physical column instead of the bare, nonexistent name.
"""
from __future__ import annotations

from types import SimpleNamespace

from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
from semabridge.connectors.schema_manager import SnowflakeSchemaManager
from semabridge.connectors.translator import MetricExpressionTranslator
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig
from semabridge.sml.models import AggregationType, DataType, SMLColumn, SMLDataset, SMLMetric
from semabridge.utils.identifiers import IdentifierSanitizer


class _DummySanitizer:
    def format_physical_column_ref(self, alias: str, col: str, model_name=None):
        return f'{alias}."{col}"'


def _build_schema_manager() -> SnowflakeSchemaManager:
    config = SnowflakeConfig(
        account="test.local", user="u", password="p", warehouse="w",
        database="SEMABRIDGE", schema_name="SEMABRIDGE_WORKSPACE", role="r",
    )
    behavior = ConnectorBehavior()
    idsan = IdentifierSanitizer(
        force_uppercase=behavior.compatibility.force_uppercase,
        always_quote=behavior.snowflake.quote_identifiers,
        suppress_reserved=behavior.compatibility.suppress_reserved_words,
    )
    return SnowflakeSchemaManager(
        config=config, behavior=behavior, identifier_sanitizer=idsan, connection_manager=None,
    )


def test_sum_unit_resolves_to_the_correct_suffixed_sibling_not_the_bare_missing_name():
    schema_manager = _build_schema_manager()

    # SALES has two distinct logical columns that both sanitize to "UNIT" --
    # the real incident's exact shape (e.g. a units-sold count and a
    # units-per-case count, or two source columns merged from Sales +
    # Returns that happened to share a sanitized name).
    dataset = SMLDataset(
        unique_name="Sales",
        source_table="Sales",
        columns=[
            SMLColumn(unique_name="Unit", data_type=DataType.INTEGER),
            SMLColumn(unique_name="UNIT", data_type=DataType.INTEGER),
        ],
    )

    # The real, already-deployed Snowflake schema: CTAS/table-creation
    # already suffixed BOTH colliding columns. There is no bare "UNIT".
    schema_manager._live_schema_metadata["SALES"] = {"UNIT_1", "UNIT_2"}

    # A direct-aggregation metric (SUM over a bare column, no DAX
    # expression) whose source_column is the raw, un-suffixed reference to
    # the SECOND of the two original columns.
    metric = SMLMetric(
        unique_name="TotalUnitQuantity",
        dataset="Sales",
        source_column="UNIT",
        aggregation=AggregationType.SUM,
    )
    model = SimpleNamespace(metrics=[metric], unique_name="SalesReturnsModel", label="SalesReturnsModel")

    builder = MetricsClauseBuilder(
        identifier_sanitizer=IdentifierSanitizer(),
        schema_manager=schema_manager,
        sanitizer=_DummySanitizer(),
        translator=MetricExpressionTranslator(identifier_sanitizer=IdentifierSanitizer()),
        config=SimpleNamespace(database="SEMABRIDGE", schema_name="SEMABRIDGE_WORKSPACE"),
    )

    lines = builder.build_for_sml(
        model,
        dataset_aliases={"Sales": "SALES"},
        dataset_by_name={"Sales": dataset},
        dataset_col_lookup={"Sales": {"UNIT_1", "UNIT_2"}},
        alias_by_raw={},
        all_physical_col_names={"UNIT_1", "UNIT_2"},
        emittable_metric_name_set={"TOTALUNITQUANTITY"},
    )

    joined = "\n".join(lines)
    assert 'SALES."UNIT_2"' in joined, (
        f"expected the metric to resolve to its own suffixed sibling UNIT_2, got:\n{joined}"
    )
    # The exact bug: must NEVER reference the bare, nonexistent "UNIT" --
    # that's the literal SQL compilation error from the incident.
    assert 'SALES."UNIT"' not in joined
