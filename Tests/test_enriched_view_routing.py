"""Regression tests verifying enriched view routing in TABLES clause and fact table enrichment discovery."""

import pytest
from semabridge.sml.models import (
    SMLModel,
    SMLDataset,
    SMLColumn,
    SMLMetric,
    SMLRelationship,
)
from semabridge.core.settings import SnowflakeConfig
from semabridge.core.behavior import ConnectorBehavior
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.connectors.tables_clause_builder import (
    TablesClauseBuilder,
    resolve_source_table_mapping,
)
from semabridge.connectors.alias_registry import AliasRegistry


def _build_test_model():
    return SMLModel(
        unique_name="TestModel",
        label="TestModel",
        datasets=[
            SMLDataset(
                unique_name="SalesFact",
                source_table="SalesFact",
                columns=[
                    SMLColumn(unique_name="Date", label="COL_DATE", data_type="date"),
                    SMLColumn(unique_name="ProductID", label="PRODUCTID", data_type="string", is_key=True),
                    SMLColumn(unique_name="Units", label="UNITS", data_type="integer", is_measure_candidate=True),
                ],
            ),
            SMLDataset(
                unique_name="Product",
                source_table="Product",
                columns=[
                    SMLColumn(unique_name="ProductID", label="PRODUCTID", data_type="string", is_key=True),
                    SMLColumn(unique_name="IsVanArsdel", label="ISVANARSDEL", data_type="string"),
                ],
            ),
            SMLDataset(
                unique_name="Date",
                source_table="Date",
                columns=[
                    SMLColumn(unique_name="Date", label="COL_DATE", data_type="date", is_key=True),
                    SMLColumn(unique_name="Year", label="YEAR", data_type="integer"),
                ],
            ),
        ],
        metrics=[
            SMLMetric(
                unique_name="TOTAL_UNITS_YTD",
                dataset="SalesFact",
                expression="TOTALYTD(SUM(SalesFact[Units]), Date[Date])",
            )
        ],
        relationships=[
            SMLRelationship(
                unique_name="rel_sales_prod",
                from_dataset="SalesFact",
                from_columns=["ProductID"],
                to_dataset="Product",
                to_columns=["ProductID"],
            ),
            SMLRelationship(
                unique_name="rel_sales_date",
                from_dataset="SalesFact",
                from_columns=["Date"],
                to_dataset="Date",
                to_columns=["Date"],
            ),
        ],
    )


def test_get_fact_tables_needing_enrichment_includes_metric_owner_datasets():
    model = _build_test_model()
    cfg = SnowflakeConfig(account="a", user="u", warehouse="w", database="d")
    behavior = ConnectorBehavior()
    emitter = SnowflakeEmitter(config=cfg, behavior=behavior)

    fact_tables = emitter._get_fact_tables_needing_enrichment(model)
    assert "SalesFact" in fact_tables or "salesfact" in [f.casefold() for f in fact_tables]


def test_tables_clause_routes_to_enriched_view_when_mapped():
    model = _build_test_model()
    cfg = SnowflakeConfig(account="TEST_DB", user="u", warehouse="w", database="TEST_DB")
    behavior = ConnectorBehavior()

    enriched_mapping = {"SalesFact": "SALESFACT_ENRICHED"}
    tbuilder = TablesClauseBuilder(
        identifier_sanitizer=SnowflakeEmitter(config=cfg, behavior=behavior)._id,
        schema_manager=SnowflakeEmitter(config=cfg, behavior=behavior).schema_manager,
        config=cfg,
        behavior=behavior,
        live_schema_metadata={},
        enriched_view_mapping=enriched_mapping,
    )
    registry = AliasRegistry()

    tables_lines, _, _, _, _, _ = tbuilder.build_for_sml(
        sml=model,
        registry=registry,
        metric_counts_by_dataset={"SalesFact": 1},
        related_datasets={"Product", "Date"},
    )
    clause = "\n".join(tables_lines)

    assert "SALESFACT_ENRICHED" in clause
    assert 'SALESFACT AS "TEST_DB"."' in clause
    assert '"SALESFACT_ENRICHED"' in clause


def test_tables_clause_routes_to_base_table_when_not_mapped():
    model = _build_test_model()
    cfg = SnowflakeConfig(account="TEST_DB", user="w", warehouse="w", database="TEST_DB")
    behavior = ConnectorBehavior()

    tbuilder = TablesClauseBuilder(
        identifier_sanitizer=SnowflakeEmitter(config=cfg, behavior=behavior)._id,
        schema_manager=SnowflakeEmitter(config=cfg, behavior=behavior).schema_manager,
        config=cfg,
        behavior=behavior,
        live_schema_metadata={},
        enriched_view_mapping={},
    )
    registry = AliasRegistry()

    tables_lines, _, _, _, _, _ = tbuilder.build_for_sml(
        sml=model,
        registry=registry,
        metric_counts_by_dataset={"SalesFact": 1},
        related_datasets={"Product", "Date"},
    )
    clause = "\n".join(tables_lines)

    assert "SALESFACT_ENRICHED" not in clause
    assert '"SALESFACT"' in clause


def test_resolve_source_table_mapping_case_insensitivity():
    behavior_mapping = {"other_table": "OTHER_CUSTOM"}
    enriched_mapping = {"SalesFact": "SALESFACT_ENRICHED"}

    resolved = resolve_source_table_mapping(behavior_mapping, enriched_mapping)

    assert resolved.get("SalesFact") == "SALESFACT_ENRICHED"
    assert resolved.get("salesfact") == "SALESFACT_ENRICHED"
    assert resolved.get("SALESFACT") == "SALESFACT_ENRICHED"
    assert resolved.get("other_table") == "OTHER_CUSTOM"


def test_dimensions_clause_includes_synthetic_enriched_view_columns():
    from semabridge.connectors.dimensions_clause_builder import DimensionsClauseBuilder
    from semabridge.connectors.semantic_ddl_sanitizer import SemanticDDLSanitizer
    from semabridge.utils.identifiers import IdentifierSanitizer
    from types import SimpleNamespace

    class _DummySchema:
        def _resolve_physical_column_name(self, dataset, column: str, model=None) -> str:
            return IdentifierSanitizer().sanitize_column(column)

    model = _build_test_model()
    id_sanitizer = IdentifierSanitizer()
    sanitizer = SemanticDDLSanitizer(id_sanitizer)
    builder = DimensionsClauseBuilder(
        id_sanitizer,
        _DummySchema(),
        sanitizer,
        translator=None,
        behavior=SimpleNamespace(semantic_model=SimpleNamespace(sync_all_attributes=True)),
    )

    dataset_aliases = {"SalesFact": "SALESFACT"}
    dataset_by_name = {"SalesFact": model.datasets[0]}
    dataset_col_lookup = {"SalesFact": {"COL_DATE", "PRODUCTID", "UNITS", "IS_YTD", "IS_SPLY_YEAR"}}

    dims_lines, _ = builder.build_for_sml(
        model,
        dataset_aliases=dataset_aliases,
        dataset_by_name=dataset_by_name,
        dataset_col_lookup=dataset_col_lookup,
        measure_columns=set(),
    )
    clause = "\n".join(dims_lines)

    assert 'SALESFACT."IS_YTD"' in clause
    assert 'SALESFACT."IS_SPLY_YEAR"' in clause


def test_dimension_alias_collision_scoping_per_dataset():
    from semabridge.connectors.dimensions_clause_builder import DimensionsClauseBuilder
    from semabridge.connectors.semantic_ddl_sanitizer import SemanticDDLSanitizer
    from semabridge.utils.identifiers import IdentifierSanitizer
    from types import SimpleNamespace

    class _DummySchema:
        def _resolve_physical_column_name(self, dataset, column: str, model=None) -> str:
            return IdentifierSanitizer().sanitize_column(column)

    model = SMLModel(
        unique_name="TestModel",
        label="TestModel",
        datasets=[
            SMLDataset(
                unique_name="Product",
                source_table="Product",
                columns=[
                    SMLColumn(unique_name="IsYTD", label="IS_YTD", data_type="string"),
                ],
            ),
            SMLDataset(
                unique_name="SalesFact",
                source_table="SalesFact",
                columns=[
                    SMLColumn(unique_name="IsYTD", label="IS_YTD", data_type="string"),
                    SMLColumn(unique_name="Zip", label="ZIP", data_type="string"),
                    SMLColumn(unique_name="ZipCode", label="ZIP", data_type="string"),
                ],
            ),
        ],
    )

    id_sanitizer = IdentifierSanitizer()
    sanitizer = SemanticDDLSanitizer(id_sanitizer)
    builder = DimensionsClauseBuilder(
        id_sanitizer,
        _DummySchema(),
        sanitizer,
        translator=None,
        behavior=SimpleNamespace(semantic_model=SimpleNamespace(sync_all_attributes=True)),
    )

    dataset_aliases = {"Product": "PRODUCT", "SalesFact": "SALESFACT"}
    dataset_by_name = {"Product": model.datasets[0], "SalesFact": model.datasets[1]}
    dataset_col_lookup = {
        "Product": {"IS_YTD"},
        "SalesFact": {"IS_YTD", "ZIP", "ZIPCODE"},
    }

    dims_lines, _ = builder.build_for_sml(
        model,
        dataset_aliases=dataset_aliases,
        dataset_by_name=dataset_by_name,
        dataset_col_lookup=dataset_col_lookup,
        measure_columns=set(),
    )
    clause = "\n".join(dims_lines)

    # 1. Both Product and SalesFact retain their own unqualified "IS_YTD" dimension name (no _2 across tables)
    assert 'PRODUCT."IS_YTD" AS PRODUCT."IS_YTD"' in clause
    assert 'SALESFACT."IS_YTD" AS SALESFACT."IS_YTD"' in clause
    assert 'SALESFACT."IS_YTD_2"' not in clause

    # 2. Intra-table collision within SalesFact still gets _2 suffix
    assert 'SALESFACT."ZIP" AS SALESFACT."ZIP"' in clause
    assert 'SALESFACT."ZIP_2" AS SALESFACT."ZIPCODE"' in clause


