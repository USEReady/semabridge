from types import SimpleNamespace

from semabridge.connectors.ddl_builder import SemanticViewBuilder


def _builder():
    return SemanticViewBuilder(
        config=SimpleNamespace(database="DB", schema_name="SCH", naming_strategy="deterministic_hash"),
        behavior=SimpleNamespace(
            snowflake=SimpleNamespace(fail_on_missing_relationships=True),
            features=SimpleNamespace(enable_cortex_analyst=False),
        ),
        identifier_sanitizer=SimpleNamespace(
            sanitize_alias=lambda s: str(s or "").upper(),
            sanitize_table_name=lambda s: str(s or "").upper(),
            sanitize_column=lambda s: str(s or "").upper(),
        ),
        live_schema_metadata={},
        schema_manager=SimpleNamespace(),
        dup_name_repo=None,
        translator=None,
    )


def test_guard_allows_present_relationships_clause():
    builder = _builder()
    ddl = """CREATE OR REPLACE SEMANTIC VIEW X
TABLES (
  A AS "DB"."SCH"."A" PRIMARY KEY ("ID")
)
RELATIONSHIPS (
  R1 AS A ("ID") REFERENCES B ("ID")
)
DIMENSIONS (
  A."ID" AS A."ID"
);"""

    builder._guard_relationship_clause(
        model_name="M",
        relationships=[
            SimpleNamespace(
                is_active=True,
                from_dataset="A",
                to_dataset="B",
                from_columns=["ID"],
                to_columns=["ID"],
            )
        ],
        semantic_ddl=ddl,
        fail_on_missing=True,
    )
