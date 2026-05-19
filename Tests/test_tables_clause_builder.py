from types import SimpleNamespace

from semabridge.connectors.tables_clause_builder import TablesClauseBuilder


class _Sanitizer:
    def sanitize_column(self, name: str) -> str:
        return str(name or "").strip().upper()

    def sanitize_table_name(self, name: str) -> str:
        return str(name or "").strip().upper()

    def sanitize_alias(self, name: str) -> str:
        return str(name or "").strip().upper().replace(" ", "_")


class _SchemaManager:
    def _collect_physical_source_columns(self, dataset):
        return {str(col.unique_name).strip().upper(): col for col in dataset.columns}

    def _resolve_physical_column_name(self, dataset, raw_col_name: str) -> str:
        return str(raw_col_name or "").strip().upper()


class _Registry:
    def __init__(self):
        self.used_table_aliases = set()
        self.dataset_aliases = {}

    def get_alias(self, name: str):
        return self.dataset_aliases.get(name)

    def register_dataset_alias(self, name: str, alias: str):
        self.dataset_aliases[name] = alias


def test_dataset_col_lookup_unions_modeled_and_live_columns():
    builder = TablesClauseBuilder(
        identifier_sanitizer=_Sanitizer(),
        schema_manager=_SchemaManager(),
        config=SimpleNamespace(database="DB", schema_name="SCH"),
        behavior=SimpleNamespace(snowflake=SimpleNamespace(pk_resolution_mode="lenient")),
        live_schema_metadata={
            # Live metadata is stale and only has old names.
            "SALESFACT": {"ZIP", "PRODUCTID"},
            "GEO": {"ZIP"},
        },
    )

    sales = SimpleNamespace(
        unique_name="SalesFact",
        source_table="SalesFact",
        columns=[SimpleNamespace(unique_name="SALES_ZIP", is_key=False)],
    )
    geo = SimpleNamespace(
        unique_name="Geo",
        source_table="Geo",
        columns=[SimpleNamespace(unique_name="GEO_ZIP", is_key=True)],
    )
    rel = SimpleNamespace(
        is_active=True,
        from_dataset="SalesFact",
        to_dataset="Geo",
        to_columns=["GEO_ZIP"],
    )

    _, _, _, dataset_col_lookup, _ = builder.build_for_sml(
        sml=SimpleNamespace(datasets=[sales, geo], relationships=[rel]),
        registry=_Registry(),
        metric_counts_by_dataset={},
        related_datasets={"SalesFact", "Geo"},
    )

    # Critical behavior: modeled columns must survive even when live metadata exists.
    assert "SALES_ZIP" in dataset_col_lookup["SalesFact"]
    assert "GEO_ZIP" in dataset_col_lookup["Geo"]
