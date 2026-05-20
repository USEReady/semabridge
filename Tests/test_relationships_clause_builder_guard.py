from types import SimpleNamespace

from semabridge.connectors.relationships_clause_builder import RelationshipsClauseBuilder


class _IdentitySanitizer:
    def sanitize_column(self, value: str) -> str:
        return value

    def to_snowflake_relationship_name(self, value: str) -> str:
        return value


class _NoopSchemaManager:
    def _resolve_physical_column_name(self, _dataset, column_name: str) -> str:
        return column_name


def test_relationships_clause_builder_skips_unkeyed_references():
    builder = RelationshipsClauseBuilder(
        identifier_sanitizer=_IdentitySanitizer(),
        schema_manager=_NoopSchemaManager(),
        sanitizer=_IdentitySanitizer(),
    )

    rel = SimpleNamespace(
        unique_name="REL_FACT_BU__BU_BU",
        from_dataset="FACT",
        from_columns=["BU"],
        to_dataset="BU",
        to_columns=["BU"],
        is_active=True,
    )
    sml = SimpleNamespace(relationships=[rel])

    lines = builder.build_for_sml(
        sml=sml,
        dataset_aliases={"FACT": "FACT", "BU": "BU"},
        dataset_by_name={"FACT": object(), "BU": object()},
        dataset_col_lookup={"FACT": {"BU"}, "BU": {"BU"}},
        declared_pk_by_alias={"FACT": ["FACT_ID"]},
        relationship_target_alias={},
    )

    assert lines == []
