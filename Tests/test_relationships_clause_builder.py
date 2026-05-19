from types import SimpleNamespace

from semabridge.connectors.relationships_clause_builder import RelationshipsClauseBuilder


class _Sanitizer:
    def sanitize_column(self, name: str) -> str:
        return str(name or "").strip().upper()


class _SchemaManager:
    def _resolve_physical_column_name(self, dataset, raw_col_name: str) -> str:
        return str(raw_col_name or "").strip().upper()


def test_relationships_builder_matches_dataset_names_case_insensitively():
    builder = RelationshipsClauseBuilder(
        identifier_sanitizer=_Sanitizer(),
        schema_manager=_SchemaManager(),
        sanitizer=SimpleNamespace(to_snowflake_relationship_name=lambda n: str(n or "").upper()),
    )

    rels = [
        SimpleNamespace(
            is_active=True,
            unique_name="rel_sales_geo_zip",
            from_dataset="SALESFACT",
            to_dataset="GEO",
            from_columns=["sales_zip"],
            to_columns=["geo_zip"],
        )
    ]
    dataset_aliases = {"SalesFact": "SALESFACT", "Geo": "GEO"}
    dataset_by_name = {
        "SalesFact": SimpleNamespace(unique_name="SalesFact"),
        "Geo": SimpleNamespace(unique_name="Geo"),
    }
    dataset_col_lookup = {"SalesFact": {"SALES_ZIP"}, "Geo": {"GEO_ZIP"}}
    declared_pk_by_alias = {"GEO": ["GEO_ZIP"]}
    relationship_target_alias = {("Geo", "GEO_ZIP"): "GEO"}

    lines = builder._build_relationships(
        relationships=rels,
        dataset_aliases=dataset_aliases,
        dataset_by_name=dataset_by_name,
        dataset_col_lookup=dataset_col_lookup,
        declared_pk_by_alias=declared_pk_by_alias,
        relationship_target_alias=relationship_target_alias,
        is_osi=False,
    )

    assert len(lines) == 1
    assert 'REFERENCES GEO ("GEO_ZIP")' in lines[0]


def test_relationships_builder_resolves_source_table_names():
    builder = RelationshipsClauseBuilder(
        identifier_sanitizer=_Sanitizer(),
        schema_manager=_SchemaManager(),
        sanitizer=SimpleNamespace(to_snowflake_relationship_name=lambda n: str(n or "").upper()),
    )

    rels = [
        SimpleNamespace(
            is_active=True,
            unique_name="rel_sales_geo_zip",
            # Relationship uses source table names, not dataset unique names.
            from_dataset="SALESFACT",
            to_dataset="GEO",
            from_columns=["sales_zip"],
            to_columns=["geo_zip"],
        )
    ]
    dataset_aliases = {"FactSalesDataset": "SALESFACT", "GeoDataset": "GEO"}
    dataset_by_name = {
        "FactSalesDataset": SimpleNamespace(unique_name="FactSalesDataset", source_table="SalesFact"),
        "GeoDataset": SimpleNamespace(unique_name="GeoDataset", source_table="Geo"),
    }
    dataset_col_lookup = {"FactSalesDataset": {"SALES_ZIP"}, "GeoDataset": {"GEO_ZIP"}}
    declared_pk_by_alias = {"GEO": ["GEO_ZIP"]}
    relationship_target_alias = {("GeoDataset", "GEO_ZIP"): "GEO"}

    lines = builder._build_relationships(
        relationships=rels,
        dataset_aliases=dataset_aliases,
        dataset_by_name=dataset_by_name,
        dataset_col_lookup=dataset_col_lookup,
        declared_pk_by_alias=declared_pk_by_alias,
        relationship_target_alias=relationship_target_alias,
        is_osi=False,
    )

    assert len(lines) == 1
    assert 'AS SALESFACT ("SALES_ZIP") REFERENCES GEO ("GEO_ZIP")' in lines[0]
