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


def test_build_for_sml_includes_inactive_relationships_for_deployment_parity():
    builder = RelationshipsClauseBuilder(
        identifier_sanitizer=_IdentitySanitizer(),
        schema_manager=_NoopSchemaManager(),
        sanitizer=_IdentitySanitizer(),
    )

    rel_active = SimpleNamespace(
        unique_name="REL_FACT_A__DIM_A",
        from_dataset="FACT",
        from_columns=["A_KEY"],
        to_dataset="DIM",
        to_columns=["A_KEY"],
        is_active=True,
    )
    rel_inactive = SimpleNamespace(
        unique_name="REL_FACT_B__DIM_B",
        from_dataset="FACT",
        from_columns=["B_KEY"],
        to_dataset="DIM",
        to_columns=["B_KEY"],
        is_active=False,
    )
    sml = SimpleNamespace(relationships=[rel_active, rel_inactive])

    lines = builder.build_for_sml(
        sml=sml,
        dataset_aliases={"FACT": "FACT", "DIM": "DIM"},
        dataset_by_name={"FACT": object(), "DIM": object()},
        dataset_col_lookup={},
        declared_pk_by_alias={},
        relationship_target_alias={},
    )

    assert len(lines) == 2
    assert any("REL_FACT_A__DIM_A" in line for line in lines)
    assert any("REL_FACT_B__DIM_B" in line for line in lines)
