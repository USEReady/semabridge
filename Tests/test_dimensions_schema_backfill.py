from types import SimpleNamespace
from unittest.mock import MagicMock

from semabridge.connectors.dimensions_clause_builder import DimensionsClauseBuilder


def test_backfills_dimensions_from_physical_schema_when_modeled_columns_are_sparse():
    id_sanitizer = MagicMock()
    id_sanitizer.sanitize_column.side_effect = lambda x: str(x).upper()

    schema_manager = MagicMock()
    schema_manager._resolve_physical_column_name.side_effect = lambda ds, col: str(col).upper()

    sanitizer = MagicMock()
    sanitizer.sanitize_semantic_name.side_effect = lambda x: str(x).upper()
    sanitizer.format_physical_column_ref.side_effect = lambda alias, col, **kwargs: f'{alias}."{col}"'

    behavior = SimpleNamespace(semantic_model=SimpleNamespace(sync_all_attributes=False))
    builder = DimensionsClauseBuilder(id_sanitizer, schema_manager, sanitizer, MagicMock(), behavior)

    dataset = SimpleNamespace(
        unique_name="Date",
        columns=[SimpleNamespace(unique_name="DATEID", is_measure_candidate=False, label="DateID")],
    )
    sml = SimpleNamespace(dimensions=[], datasets=[dataset], unique_name="model", label="model")

    dims = builder.build_for_sml(
        sml=sml,
        dataset_aliases={"Date": "DATE"},
        dataset_by_name={"Date": dataset},
        dataset_col_lookup={"Date": {"DATEID", "MONTHNO", "MONTHNAME", "YEAR", "REVENUE"}},
        measure_columns={("date", "REVENUE")},
    )

    ddl = "\n".join(dims)
    assert 'DATE."MONTHNO" AS DATE."MONTHNO"' in ddl
    assert 'DATE."MONTHNAME" AS DATE."MONTHNAME"' in ddl
    assert 'DATE."YEAR" AS DATE."YEAR"' in ddl
    assert 'DATE."REVENUE" AS DATE."REVENUE"' not in ddl

