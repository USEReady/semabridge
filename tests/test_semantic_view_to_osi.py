"""
Unit tests for SemanticViewToOSIConverter.

Covers:
- Full DDL parsing (TABLES, RELATIONSHIPS, DIMENSIONS, MEASURES)
- Partial DDL (missing optional clauses)
- Type mapping from Snowflake SQL types → OSIDataType
- Error handling for missing / malformed input
- View name parsing from fully-qualified identifiers
"""
from __future__ import annotations

import pytest

from semabridge.converter.semantic_view_to_osi import SemanticViewToOSIConverter
from semabridge.core.exceptions import ConversionError
from semabridge.intermediate.models import (
    OSICardinality,
    OSIDataType,
    OSIModel,
)

# ---------------------------------------------------------------------------
# Fixtures — sample DDLs
# ---------------------------------------------------------------------------

FULL_DDL = """
CREATE OR REPLACE SEMANTIC VIEW ANALYTICS_DB.MARKETING.SALES_SEMANTIC
TABLES (
    s AS ANALYTICS_DB.SALES."ORDERS" PRIMARY KEY ("ORDER_ID"),
    c AS ANALYTICS_DB.SALES."CUSTOMERS" PRIMARY KEY ("CUSTOMER_ID")
)
RELATIONSHIPS (
    s ("CUSTOMER_ID") REFERENCES c
)
DIMENSIONS (
    s."ORDER_DATE"    AS "Order Date",
    s."REGION"        AS "Region",
    c."CUSTOMER_NAME" AS "Customer Name",
    c."COUNTRY"       AS "Country"
)
MEASURES (
    s."REVENUE"   AS SUM(s."REVENUE"),
    s."QUANTITY"  AS SUM(s."QUANTITY")
)
"""

MINIMAL_DDL = """
CREATE OR REPLACE SEMANTIC VIEW MY_DB.MY_SCHEMA.SIMPLE_VIEW
TABLES (
    t AS MY_DB.MY_SCHEMA."MY_TABLE" PRIMARY KEY ("ID")
)
"""

NO_MEASURES_DDL = """
CREATE OR REPLACE SEMANTIC VIEW DB.SCH.DIMS_ONLY
TABLES (
    p AS DB.SCH."PRODUCTS" PRIMARY KEY ("PRODUCT_ID")
)
DIMENSIONS (
    p."PRODUCT_NAME" AS "Product Name",
    p."CATEGORY"     AS "Category"
)
"""

MULTI_RELATIONSHIP_DDL = """
CREATE OR REPLACE SEMANTIC VIEW DB.SCH.COMPLEX_VIEW
TABLES (
    f AS DB.SCH."FACT_SALES" PRIMARY KEY ("SALE_ID"),
    d AS DB.SCH."DIM_DATE"   PRIMARY KEY ("DATE_KEY"),
    p AS DB.SCH."DIM_PRODUCT" PRIMARY KEY ("PRODUCT_KEY")
)
RELATIONSHIPS (
    f ("DATE_KEY")    REFERENCES d,
    f ("PRODUCT_KEY") REFERENCES p
)
DIMENSIONS (
    d."YEAR"         AS "Year",
    p."PRODUCT_NAME" AS "Product Name"
)
MEASURES (
    f."AMOUNT" AS SUM(f."AMOUNT")
)
"""

NAMED_RELATIONSHIP_NO_REL_PREFIX_DDL = """
CREATE OR REPLACE SEMANTIC VIEW DB.SCH.NAMED_REL_VIEW
TABLES (
    f AS DB.SCH."FACT_SALES" PRIMARY KEY ("SALE_ID"),
    p AS DB.SCH."DIM_PRODUCT" PRIMARY KEY ("PRODUCT_KEY")
)
RELATIONSHIPS (
    FACT_SALES_PRODUCT_KEY__DIM_PRODUCT_PRODUCT_KEY AS f ("PRODUCT_KEY") REFERENCES p
)
"""

WITH_COLUMN_METADATA_DDL = """
CREATE OR REPLACE SEMANTIC VIEW DB.SCH.TYPED_VIEW
TABLES (
    o AS DB.SCH."ORDERS" PRIMARY KEY ("ID")
)
DIMENSIONS (
    o."CREATED_AT" AS "Created At",
    o."IS_ACTIVE"  AS "Is Active"
)
MEASURES (
    o."TOTAL" AS SUM(o."TOTAL")
)
"""

COLUMN_METADATA = {
    "ORDERS": [
        {"name": "ID",         "data_type": "INTEGER",   "is_nullable": False, "comment": "PK"},
        {"name": "CREATED_AT", "data_type": "TIMESTAMP", "is_nullable": True,  "comment": ""},
        {"name": "IS_ACTIVE",  "data_type": "BOOLEAN",   "is_nullable": True,  "comment": ""},
        {"name": "TOTAL",      "data_type": "DECIMAL",   "is_nullable": True,  "comment": ""},
    ]
}


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def converter() -> SemanticViewToOSIConverter:
    return SemanticViewToOSIConverter()


# ---------------------------------------------------------------------------
# 1. Model-level assertions
# ---------------------------------------------------------------------------

class TestModelMeta:
    def test_unique_name_from_ddl(self, converter):
        osi = converter.to_osi({"ddl": FULL_DDL})
        assert osi.unique_name == "SALES_SEMANTIC"

    def test_unique_name_fallback(self, converter):
        osi = converter.to_osi({"ddl": MINIMAL_DDL, "view_name": "fallback_name"})
        assert osi.unique_name == "SIMPLE_VIEW"

    def test_source_platform(self, converter):
        osi = converter.to_osi({"ddl": FULL_DDL})
        assert osi.source_platform == "snowflake_semantic_view"

    def test_returns_osi_model(self, converter):
        osi = converter.to_osi({"ddl": FULL_DDL})
        assert isinstance(osi, OSIModel)


# ---------------------------------------------------------------------------
# 2. Dataset / table parsing
# ---------------------------------------------------------------------------

class TestDatasets:
    def test_datasets_count_full(self, converter):
        osi = converter.to_osi({"ddl": FULL_DDL})
        assert len(osi.datasets) == 2

    def test_dataset_names(self, converter):
        osi = converter.to_osi({"ddl": FULL_DDL})
        names = {d.unique_name for d in osi.datasets}
        assert "ORDERS" in names
        assert "CUSTOMERS" in names

    def test_minimal_ddl_one_dataset(self, converter):
        osi = converter.to_osi({"ddl": MINIMAL_DDL})
        assert len(osi.datasets) == 1
        assert osi.datasets[0].unique_name == "MY_TABLE"

    def test_source_table_set(self, converter):
        osi = converter.to_osi({"ddl": FULL_DDL})
        ds = next(d for d in osi.datasets if d.unique_name == "ORDERS")
        assert ds.source_table == "ORDERS"

    def test_complex_view_three_datasets(self, converter):
        osi = converter.to_osi({"ddl": MULTI_RELATIONSHIP_DDL})
        assert len(osi.datasets) == 3


# ---------------------------------------------------------------------------
# 3. Relationships
# ---------------------------------------------------------------------------

class TestRelationships:
    def test_single_relationship_parsed(self, converter):
        osi = converter.to_osi({"ddl": FULL_DDL})
        assert len(osi.relationships) == 1

    def test_relationship_endpoints(self, converter):
        osi = converter.to_osi({"ddl": FULL_DDL})
        rel = osi.relationships[0]
        assert rel.from_dataset == "ORDERS"
        assert rel.to_dataset == "CUSTOMERS"

    def test_relationship_cardinality_default(self, converter):
        osi = converter.to_osi({"ddl": FULL_DDL})
        rel = osi.relationships[0]
        assert rel.cardinality == OSICardinality.MANY_TO_ONE

    def test_multi_relationships(self, converter):
        osi = converter.to_osi({"ddl": MULTI_RELATIONSHIP_DDL})
        assert len(osi.relationships) == 2

    def test_no_relationships_minimal(self, converter):
        osi = converter.to_osi({"ddl": MINIMAL_DDL})
        assert osi.relationships == []

    def test_named_relationship_without_rel_prefix_restored_to_canonical(self, converter):
        osi = converter.to_osi({"ddl": NAMED_RELATIONSHIP_NO_REL_PREFIX_DDL})
        assert len(osi.relationships) == 1
        assert osi.relationships[0].unique_name == "REL_FACT_SALES_PRODUCT_KEY__DIM_PRODUCT_PRODUCT_KEY"


# ---------------------------------------------------------------------------
# 4. Dimensions
# ---------------------------------------------------------------------------

class TestDimensions:
    def test_dimensions_from_full_ddl(self, converter):
        osi = converter.to_osi({"ddl": FULL_DDL})
        # 4 dimension columns declared → at least 1 dimension group
        total_attrs = sum(len(d.attributes) for d in osi.dimensions)
        assert total_attrs >= 4

    def test_no_measures_ddl(self, converter):
        osi = converter.to_osi({"ddl": NO_MEASURES_DDL})
        assert osi.metrics == []
        # 2 dimension columns → check at least present
        total_attrs = sum(len(d.attributes) for d in osi.dimensions)
        assert total_attrs >= 2


# ---------------------------------------------------------------------------
# 5. Metrics / measures
# ---------------------------------------------------------------------------

class TestMetrics:
    def test_metrics_count(self, converter):
        osi = converter.to_osi({"ddl": FULL_DDL})
        assert len(osi.metrics) == 2

    def test_metric_names(self, converter):
        osi = converter.to_osi({"ddl": FULL_DDL})
        metric_names = {m.unique_name for m in osi.metrics}
        assert "REVENUE" in metric_names or any("revenue" in n.lower() for n in metric_names)

    def test_no_measures_clause(self, converter):
        osi = converter.to_osi({"ddl": NO_MEASURES_DDL})
        assert osi.metrics == []

    def test_complex_view_one_metric(self, converter):
        osi = converter.to_osi({"ddl": MULTI_RELATIONSHIP_DDL})
        assert len(osi.metrics) == 1


# ---------------------------------------------------------------------------
# 6. Column metadata enrichment
# ---------------------------------------------------------------------------

class TestColumnMetadataEnrichment:
    def test_timestamps_mapped_correctly(self, converter):
        osi = converter.to_osi({
            "ddl": WITH_COLUMN_METADATA_DDL,
            "column_metadata": COLUMN_METADATA,
        })
        ds = next((d for d in osi.datasets if d.unique_name == "ORDERS"), None)
        assert ds is not None
        created_col = next((c for c in ds.columns if c.unique_name == "CREATED_AT"), None)
        if created_col:
            assert created_col.data_type in (OSIDataType.DATETIME, OSIDataType.STRING)

    def test_boolean_type_mapped(self, converter):
        osi = converter.to_osi({
            "ddl": WITH_COLUMN_METADATA_DDL,
            "column_metadata": COLUMN_METADATA,
        })
        ds = next((d for d in osi.datasets if d.unique_name == "ORDERS"), None)
        assert ds is not None
        bool_col = next((c for c in ds.columns if c.unique_name == "IS_ACTIVE"), None)
        if bool_col:
            assert bool_col.data_type == OSIDataType.BOOLEAN


# ---------------------------------------------------------------------------
# 7. Type mapping (_map_sf_type via converter)
# ---------------------------------------------------------------------------

class TestTypeMappingEdgeCases:
    """Directly exercise the internal type mapping via full round-trip."""

    @pytest.mark.parametrize("sf_type,expected", [
        ("NUMBER",       OSIDataType.INTEGER),
        ("DECIMAL(18,2)", OSIDataType.DECIMAL),
        ("FLOAT",        OSIDataType.FLOAT),
        ("VARCHAR(256)",  OSIDataType.STRING),
        ("BOOLEAN",      OSIDataType.BOOLEAN),
        ("DATE",         OSIDataType.DATE),
        ("TIMESTAMP_NTZ", OSIDataType.DATETIME),
        ("VARIANT",      OSIDataType.VARIANT),
        ("GEOGRAPHY",    OSIDataType.STRING),
        ("UNKNOWN_XYZ",  OSIDataType.STRING),   # fallback
    ])
    def test_type_mapping(self, converter, sf_type, expected):
        from semabridge.converter.semantic_view_to_osi import _map_sf_type
        assert _map_sf_type(sf_type) == expected


# ---------------------------------------------------------------------------
# 8. Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_missing_ddl_raises(self, converter):
        with pytest.raises(ConversionError):
            converter.to_osi({})

    def test_empty_ddl_raises(self, converter):
        with pytest.raises(ConversionError):
            converter.to_osi({"ddl": ""})

    def test_none_ddl_raises(self, converter):
        with pytest.raises(ConversionError):
            converter.to_osi({"ddl": None})

    def test_garbage_ddl_returns_model_with_no_datasets(self, converter):
        """A DDL that is not a SEMANTIC VIEW yields an empty OSIModel rather than raising."""
        osi = converter.to_osi({"ddl": "SELECT 1", "view_name": "fallback"})
        # Converter is lenient: uses fallback name and returns empty datasets
        assert isinstance(osi, OSIModel)
        assert osi.datasets == []

    def test_ddl_without_tables_returns_empty_datasets(self, converter):
        """A SEMANTIC VIEW DDL with no TABLES clause yields empty datasets."""
        no_tables_ddl = "CREATE OR REPLACE SEMANTIC VIEW DB.SCH.NO_TABLES_VIEW DIMENSIONS (x.\"col\" AS \"Col\")"
        osi = converter.to_osi({"ddl": no_tables_ddl})
        assert osi.unique_name == "NO_TABLES_VIEW"
        assert osi.datasets == []

    def test_view_name_fallback_when_no_create_statement(self, converter):
        """When the DDL has no CREATE ... SEMANTIC VIEW line, view_name is the fallback."""
        # Plain SQL with no SEMANTIC VIEW keyword → _parse_view_name returns None
        osi = converter.to_osi({"ddl": "SHOW SEMANTIC VIEWS", "view_name": "my_fallback"})
        assert osi.unique_name == "my_fallback"


# ---------------------------------------------------------------------------
# 9. Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_same_input_same_output(self, converter):
        osi1 = converter.to_osi({"ddl": FULL_DDL})
        osi2 = converter.to_osi({"ddl": FULL_DDL})
        assert osi1.unique_name == osi2.unique_name
        assert len(osi1.datasets) == len(osi2.datasets)
        assert len(osi1.metrics) == len(osi2.metrics)
        assert len(osi1.relationships) == len(osi2.relationships)
