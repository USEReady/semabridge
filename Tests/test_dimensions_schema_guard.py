from types import SimpleNamespace
from unittest.mock import MagicMock

from semabridge.connectors.dimensions_clause_builder import DimensionsClauseBuilder


def test_explicit_modeled_dimensions_are_dropped_when_missing_from_live_schema_by_default():
    id_sanitizer = MagicMock()
    id_sanitizer.sanitize_column.side_effect = lambda x: str(x).upper()

    schema_manager = MagicMock()
    schema_manager._resolve_physical_column_name.side_effect = lambda ds, col: str(col).upper()

    sanitizer = MagicMock()
    sanitizer.sanitize_semantic_name.side_effect = lambda x: str(x).upper()
    sanitizer.format_physical_column_ref.side_effect = lambda alias, col, **kwargs: f'{alias}."{col}"'

    behavior = SimpleNamespace(semantic_model=SimpleNamespace(sync_all_attributes=True))
    builder = DimensionsClauseBuilder(id_sanitizer, schema_manager, sanitizer, MagicMock(), behavior)

    dataset = SimpleNamespace(
        unique_name="Product",
        columns=[
            SimpleNamespace(unique_name="PRODUCTID", is_measure_candidate=False, label="ProductID"),
            SimpleNamespace(unique_name="SEGMENT", is_measure_candidate=False, label="Segment"),
            SimpleNamespace(unique_name="PRODUCT", is_measure_candidate=False, label="Product"),
        ],
        get_column=lambda name: None,
    )
    attr_segment = SimpleNamespace(
        dataset="Product",
        unique_name="Segment",
        dataset_column="SEGMENT",
        source_column="SEGMENT",
    )
    dim = SimpleNamespace(attributes=[attr_segment])
    sml = SimpleNamespace(dimensions=[dim], datasets=[dataset], unique_name="model", label="model")

    dims = builder.build_for_sml(
        sml=sml,
        dataset_aliases={"Product": "PRODUCT"},
        dataset_by_name={"Product": dataset},
        dataset_col_lookup={"Product": {"PRODUCTID"}},  # stale/incomplete live schema
        measure_columns=set(),
    )

    ddl = "\n".join(dims)
    assert 'PRODUCT."SEGMENT" AS PRODUCT."SEGMENT"' not in ddl


def test_explicit_modeled_dimensions_can_be_emitted_when_opt_in_flag_is_enabled():
    id_sanitizer = MagicMock()
    id_sanitizer.sanitize_column.side_effect = lambda x: str(x).upper()

    schema_manager = MagicMock()
    schema_manager._resolve_physical_column_name.side_effect = lambda ds, col: str(col).upper()

    sanitizer = MagicMock()
    sanitizer.sanitize_semantic_name.side_effect = lambda x: str(x).upper()
    sanitizer.format_physical_column_ref.side_effect = lambda alias, col, **kwargs: f'{alias}."{col}"'

    behavior = SimpleNamespace(
        semantic_model=SimpleNamespace(
            sync_all_attributes=True,
            allow_emit_modeled_missing_columns=True,
        )
    )
    builder = DimensionsClauseBuilder(id_sanitizer, schema_manager, sanitizer, MagicMock(), behavior)

    dataset = SimpleNamespace(
        unique_name="Product",
        columns=[
            SimpleNamespace(unique_name="PRODUCTID", is_measure_candidate=False, label="ProductID"),
            SimpleNamespace(unique_name="SEGMENT", is_measure_candidate=False, label="Segment"),
            SimpleNamespace(unique_name="PRODUCT", is_measure_candidate=False, label="Product"),
        ],
        get_column=lambda name: None,
    )
    attr_segment = SimpleNamespace(
        dataset="Product",
        unique_name="Segment",
        dataset_column="SEGMENT",
        source_column="SEGMENT",
    )
    dim = SimpleNamespace(attributes=[attr_segment])
    sml = SimpleNamespace(dimensions=[dim], datasets=[dataset], unique_name="model", label="model")

    dims = builder.build_for_sml(
        sml=sml,
        dataset_aliases={"Product": "PRODUCT"},
        dataset_by_name={"Product": dataset},
        dataset_col_lookup={"Product": {"PRODUCTID"}},
        measure_columns=set(),
    )

    ddl = "\n".join(dims)
    assert 'PRODUCT."SEGMENT" AS PRODUCT."SEGMENT"' in ddl


def test_explicit_dimensions_with_colliding_aliases_are_preserved_with_opt_in_flag():
    id_sanitizer = MagicMock()
    id_sanitizer.sanitize_column.side_effect = lambda x: str(x).upper()

    schema_manager = MagicMock()
    schema_manager._resolve_physical_column_name.side_effect = lambda ds, col: str(col).upper()

    sanitizer = MagicMock()
    sanitizer.sanitize_semantic_name.side_effect = lambda x: str(x).upper()
    sanitizer.format_physical_column_ref.side_effect = lambda alias, col, **kwargs: f'{alias}."{col}"'

    behavior = SimpleNamespace(
        semantic_model=SimpleNamespace(
            sync_all_attributes=True,
            allow_emit_modeled_missing_columns=True,
        )
    )
    builder = DimensionsClauseBuilder(id_sanitizer, schema_manager, sanitizer, MagicMock(), behavior)

    kpi_dataset = SimpleNamespace(
        unique_name="KPI",
        columns=[
            SimpleNamespace(unique_name="KPI", is_measure_candidate=False, label="KPI"),
            SimpleNamespace(unique_name="CATEGORY", is_measure_candidate=False, label="Category"),
        ],
        get_column=lambda name: None,
    )
    category_dataset = SimpleNamespace(
        unique_name="CATEGORY",
        columns=[
            SimpleNamespace(unique_name="CATEGORY", is_measure_candidate=False, label="Category"),
            SimpleNamespace(unique_name="CHANNEL", is_measure_candidate=False, label="Channel"),
        ],
        get_column=lambda name: None,
    )

    attr_kpi_category = SimpleNamespace(
        dataset="KPI",
        unique_name="CATEGORY",
        dataset_column="CATEGORY",
        source_column="CATEGORY",
    )
    attr_category_category = SimpleNamespace(
        dataset="CATEGORY",
        unique_name="CATEGORY",
        dataset_column="CATEGORY",
        source_column="CATEGORY",
    )
    sml = SimpleNamespace(
        dimensions=[
            SimpleNamespace(attributes=[attr_kpi_category]),
            SimpleNamespace(attributes=[attr_category_category]),
        ],
        datasets=[kpi_dataset, category_dataset],
        unique_name="model",
        label="model",
    )

    dims = builder.build_for_sml(
        sml=sml,
        dataset_aliases={"KPI": "KPI", "CATEGORY": "CATEGORY"},
        dataset_by_name={"KPI": kpi_dataset, "CATEGORY": category_dataset},
        dataset_col_lookup={
            "KPI": {"KPI"},
            "CATEGORY": {"CHANNEL"},
        },
        measure_columns=set(),
    )

    ddl = "\n".join(dims)
    assert 'KPI."CATEGORY" AS KPI."CATEGORY"' in ddl
    assert 'CATEGORY."CATEGORY_' in ddl
    assert ' AS CATEGORY."CATEGORY"' in ddl


def test_relationship_join_keys_are_not_emitted_as_dimensions():
    id_sanitizer = MagicMock()
    id_sanitizer.sanitize_column.side_effect = lambda x: str(x).upper()

    schema_manager = MagicMock()
    schema_manager._resolve_physical_column_name.side_effect = lambda ds, col: str(col).upper()

    sanitizer = MagicMock()
    sanitizer.sanitize_semantic_name.side_effect = lambda x: str(x).upper()
    sanitizer.format_physical_column_ref.side_effect = lambda alias, col, **kwargs: f'{alias}."{col}"'

    behavior = SimpleNamespace(semantic_model=SimpleNamespace(sync_all_attributes=True))
    builder = DimensionsClauseBuilder(id_sanitizer, schema_manager, sanitizer, MagicMock(), behavior)

    sales_dataset = SimpleNamespace(
        unique_name="SALESFACT",
        columns=[
            SimpleNamespace(unique_name="PRODUCTID", is_measure_candidate=False, label="ProductID"),
            SimpleNamespace(unique_name="ZIP", is_measure_candidate=False, label="Zip"),
            SimpleNamespace(unique_name="UNITS", is_measure_candidate=False, label="Units"),
        ],
        get_column=lambda name: None,
    )
    sml = SimpleNamespace(dimensions=[], datasets=[sales_dataset], unique_name="model", label="model")

    dims = builder.build_for_sml(
        sml=sml,
        dataset_aliases={"SALESFACT": "SALESFACT"},
        dataset_by_name={"SALESFACT": sales_dataset},
        dataset_col_lookup={"SALESFACT": {"PRODUCTID", "ZIP", "UNITS"}},
        measure_columns=set(),
        relationship_columns={("salesfact", "PRODUCTID"), ("salesfact", "ZIP")},
    )

    ddl = "\n".join(dims)
    assert 'SALESFACT."PRODUCTID" AS SALESFACT."PRODUCTID"' not in ddl
    assert 'SALESFACT."ZIP" AS SALESFACT."ZIP"' not in ddl
    assert 'SALESFACT."UNITS" AS SALESFACT."UNITS"' in ddl
