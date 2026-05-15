from semabridge.connectors.tmsl_generator import TMSLGenerator
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.converter.semantic_view_to_osi import SemanticViewToOSIConverter


def test_semantic_view_relationship_labels_are_canonicalized_for_osi():
    ddl = """
    CREATE OR REPLACE SEMANTIC VIEW "DB"."PUBLIC"."MODEL_SEMANTIC"
    TABLES (
      PRODUCT AS "DB"."PUBLIC"."PRODUCT" PRIMARY KEY ("PRODUCTID"),
      CATEGORY AS "DB"."PUBLIC"."CATEGORY" PRIMARY KEY ("CATEGORY")
    )
    RELATIONSHIPS (
      PRODUCT_SEGMENT_CATEGORY_CATEGORY AS PRODUCT ("SEGMENT") REFERENCES CATEGORY ("CATEGORY")
    )
    DIMENSIONS (
      PRODUCT."SEGMENT" AS PRODUCT."SEGMENT",
      CATEGORY."CATEGORY" AS CATEGORY."CATEGORY"
    )
    METRICS (
      PRODUCT."COUNT_OF_PRODUCT" AS COUNT(PRODUCT.PRODUCT)
    );
    """

    osi = SemanticViewToOSIConverter().to_osi(
        {"ddl": ddl, "view_name": "MODEL_SEMANTIC"}
    )

    assert [rel.unique_name for rel in osi.relationships] == [
        "REL_PRODUCT_SEGMENT__CATEGORY_CATEGORY"
    ]
    assert len(osi.metrics) == 1


def test_semantic_view_relationships_and_metrics_reach_fabric_model_bim():
    ddl = """
    CREATE OR REPLACE SEMANTIC VIEW "DB"."PUBLIC"."MODEL_SEMANTIC"
    TABLES (
      PRODUCT AS "DB"."PUBLIC"."PRODUCT" PRIMARY KEY ("PRODUCTID"),
      CATEGORY AS "DB"."PUBLIC"."CATEGORY" PRIMARY KEY ("CATEGORY"),
      SALESFACT AS "DB"."PUBLIC"."SALESFACT" PRIMARY KEY ("PRODUCTID")
    )
    RELATIONSHIPS (
      PRODUCT_SEGMENT_CATEGORY_CATEGORY AS PRODUCT ("SEGMENT") REFERENCES CATEGORY ("CATEGORY"),
      SALESFACT_PRODUCTID_PRODUCT_PRODUCTID AS SALESFACT ("PRODUCTID") REFERENCES PRODUCT ("PRODUCTID")
    )
    DIMENSIONS (
      PRODUCT."SEGMENT" AS PRODUCT."SEGMENT",
      CATEGORY."CATEGORY" AS CATEGORY."CATEGORY",
      SALESFACT."UNITS" AS SALESFACT."UNITS"
    )
    METRICS (
      PRODUCT."COUNT_OF_PRODUCT" AS COUNT(PRODUCT.PRODUCT),
      SALESFACT."TOTAL_UNITS" AS SUM(SALESFACT.UNITS)
    );
    """

    osi = SemanticViewToOSIConverter().to_osi(
        {"ddl": ddl, "view_name": "MODEL_SEMANTIC"}
    )
    sml = OSIToSMLConverter().from_osi(osi)
    model_bim = TMSLGenerator(
        sml,
        snowflake_server="account",
        snowflake_warehouse="warehouse",
        snowflake_database="DB",
        snowflake_schema="PUBLIC",
    ).generate()

    measures = [
        measure
        for table in model_bim["model"]["tables"]
        for measure in table.get("measures", [])
    ]

    assert len(model_bim["model"]["relationships"]) == 2
    assert len(measures) == 2


def test_semantic_view_ddl_columns_are_merged_when_live_metadata_is_narrower():
    ddl = """
    CREATE OR REPLACE SEMANTIC VIEW "DB"."PUBLIC"."MODEL_SEMANTIC"
    TABLES (
      PRODUCT AS "DB"."PUBLIC"."PRODUCT" PRIMARY KEY ("PRODUCTID"),
      CATEGORY AS "DB"."PUBLIC"."CATEGORY" PRIMARY KEY ("CATEGORY"),
      SALESFACT AS "DB"."PUBLIC"."SALESFACT" PRIMARY KEY ("PRODUCTID"),
      MANUFACTURER AS "DB"."PUBLIC"."MANUFACTURER" PRIMARY KEY ("MANUFACTURERID")
    )
    RELATIONSHIPS (
      PRODUCT_SEGMENT_CATEGORY_CATEGORY AS PRODUCT ("SEGMENT") REFERENCES CATEGORY ("CATEGORY"),
      PRODUCT_MANUFACTURERID_MANUFACTURER_MANUFACTURERID AS PRODUCT ("MANUFACTURERID") REFERENCES MANUFACTURER ("MANUFACTURERID"),
      SALESFACT_PRODUCTID_PRODUCT_PRODUCTID AS SALESFACT ("PRODUCTID") REFERENCES PRODUCT ("PRODUCTID")
    )
    DIMENSIONS (
      PRODUCT."PRODUCT_KEY" AS PRODUCT."PRODUCTID",
      PRODUCT."SEGMENT" AS PRODUCT."SEGMENT",
      PRODUCT."MANUFACTURERID_2" AS PRODUCT."MANUFACTURERID",
      PRODUCT."PRODUCT" AS PRODUCT."PRODUCT",
      CATEGORY."CATEGORY" AS CATEGORY."CATEGORY",
      SALESFACT."PRODUCTID" AS SALESFACT."PRODUCTID",
      MANUFACTURER."MANUFACTURERID" AS MANUFACTURER."MANUFACTURERID"
    )
    METRICS (
      SALESFACT."TOTAL_UNITS" AS SUM(SALESFACT.UNITS)
    );
    """

    osi = SemanticViewToOSIConverter().to_osi(
        {
            "ddl": ddl,
            "view_name": "MODEL_SEMANTIC",
            "column_metadata": {
                "PRODUCT": [
                    {"name": "PRODUCT_KEY", "data_type": "NUMBER"},
                    {"name": "PRODUCT", "data_type": "VARCHAR"},
                ],
                "CATEGORY": [{"name": "CATEGORY", "data_type": "VARCHAR"}],
                "SALESFACT": [{"name": "PRODUCTID", "data_type": "NUMBER"}],
                "MANUFACTURER": [{"name": "MANUFACTURERID", "data_type": "NUMBER"}],
            },
        }
    )
    sml = OSIToSMLConverter().from_osi(osi)
    model_bim = TMSLGenerator(
        sml,
        snowflake_server="account",
        snowflake_warehouse="warehouse",
        snowflake_database="DB",
        snowflake_schema="PUBLIC",
    ).generate()

    columns_by_table = {
        table["name"]: {column["name"] for column in table.get("columns", [])}
        for table in model_bim["model"]["tables"]
    }

    for relationship in model_bim["model"]["relationships"]:
        assert relationship["fromColumn"] in columns_by_table[relationship["fromTable"]]
        assert relationship["toColumn"] in columns_by_table[relationship["toTable"]]

    product_table = next(
        table for table in model_bim["model"]["tables"] if table["name"] == "PRODUCT"
    )
    product_query = product_table["partitions"][0]["source"]["expression"]

    assert '""PRODUCT_KEY"" AS ""PRODUCTID""' in product_query
    assert '""MANUFACTURERID_2"" AS ""MANUFACTURERID""' in product_query
