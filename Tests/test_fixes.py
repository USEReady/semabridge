from __future__ import annotations

from types import SimpleNamespace

from semabridge.converter.dax_translator import DAXTranslator
from semabridge.connectors.semantic_ddl_sanitizer import SemanticDDLSanitizer
from semabridge.converter.tmdl_to_osi import TMDLToOSIConverter, analyze_tmdl_tables


class DummyIdentifierSanitizer:
        _reserved: set[str] = set()

        def sanitize_column(self, name: str) -> str:
                return str(name or "").replace(" ", "_").replace("-", "_").upper()

        def sanitize_alias(self, name: str) -> str:
                return self.sanitize_column(name)


def _build_tmdl_files() -> dict[str, str]:
        return {
                "definition/tables/SalesFact.tmdl": """
table SalesFact
isHidden
column ProductID
    dataType: int64
column DateID
    dataType: int64
column GeoID
    dataType: int64
column Revenue
    dataType: double
column Cost
    dataType: double
measure Sales = SUM(SalesFact[Revenue])
measure GrossMargin = [Sales] - SUM(SalesFact[Cost])
""",
                "definition/tables/Sentiment.tmdl": """
table Sentiment
isHidden
column GeoID
    dataType: int64
column DateID
    dataType: int64
column ManufacturerID
    dataType: int64
column Score
    dataType: double
measure SentimentScore = AVERAGE(Sentiment[Score])
""",
                "definition/tables/Product.tmdl": """
table Product
column ProductID
    dataType: int64
column ManufacturerID
    dataType: int64
column ProductName
    dataType: string
""",
                "definition/tables/Date.tmdl": """
table Date
column DateID
    dataType: int64
column Year
    dataType: int64
""",
                "definition/tables/Geo.tmdl": """
table Geo
column GeoID
    dataType: int64
column GeoName
    dataType: string
""",
                "definition/tables/Manufacturer.tmdl": """
table Manufacturer
column ManufacturerID
    dataType: int64
column ManufacturerName
    dataType: string
""",
                "definition/tables/Indicators.tmdl": """
table Indicators
column IndicatorID
    dataType: int64
column IndicatorName
    dataType: string
""",
                "definition/tables/KPI.tmdl": """
table KPI
column KPIID
    dataType: int64
column KpiName
    dataType: string
measure GrossMarginPct = DIVIDE([GrossMargin], [Sales])
""",
                "definition/tables/Category.tmdl": """
table Category
column CategoryID
    dataType: int64
column CategoryName
    dataType: string
""",
                "definition/tables/DateTableTemplate_1.tmdl": """
table DateTableTemplate_1
isHidden
__PBI_TemplateDateTable = true
column DateID
    dataType: int64
""",
                "definition/tables/LocalDateTable_1.tmdl": """
table LocalDateTable_1
isHidden
__PBI_LocalDateTable = true
column DateID
    dataType: int64
""",
                "definition/tables/Relationships.tmdl": """
table Relationships
relationship SalesFact_Product
    fromTable: SalesFact
    fromColumn: ProductID
    toTable: Product
    toColumn: ProductID
    cardinality: manyToOne
    crossFilteringBehavior: singleDirection
relationship SalesFact_Date
    fromTable: SalesFact
    fromColumn: DateID
    toTable: Date
    toColumn: DateID
    cardinality: manyToOne
    crossFilteringBehavior: singleDirection
relationship SalesFact_Geo
    fromTable: SalesFact
    fromColumn: GeoID
    toTable: Geo
    toColumn: GeoID
    cardinality: manyToOne
    crossFilteringBehavior: singleDirection
relationship Sentiment_Geo
    fromTable: Sentiment
    fromColumn: GeoID
    toTable: Geo
    toColumn: GeoID
    cardinality: manyToOne
    crossFilteringBehavior: singleDirection
relationship Sentiment_Date
    fromTable: Sentiment
    fromColumn: DateID
    toTable: Date
    toColumn: DateID
    cardinality: manyToOne
    crossFilteringBehavior: singleDirection
relationship Sentiment_Manufacturer
    fromTable: Sentiment
    fromColumn: ManufacturerID
    toTable: Manufacturer
    toColumn: ManufacturerID
    cardinality: manyToOne
    crossFilteringBehavior: singleDirection
relationship Product_Manufacturer
    fromTable: Product
    fromColumn: ManufacturerID
    toTable: Manufacturer
    toColumn: ManufacturerID
    cardinality: manyToOne
    crossFilteringBehavior: singleDirection
relationship Category_Product
    fromTable: Category
    fromColumn: CategoryID
    toTable: Product
    toColumn: ProductID
    cardinality: manyToOne
    crossFilteringBehavior: singleDirection
relationship Date_Variation
    fromTable: Date
    fromColumn: DateID
    toTable: Product
    toColumn: ProductID
    cardinality: manyToOne
    crossFilteringBehavior: singleDirection
    joinOnDateBehavior: datePartOnly
""",
        }


def test_hidden_fact_tables_are_preserved_and_system_tables_are_excluded() -> None:
        tmdl_files = _build_tmdl_files()
        records = analyze_tmdl_tables(tmdl_files)
        by_name = {record["name"]: record for record in records}

        assert by_name["SalesFact"]["include"] is True
        assert by_name["SalesFact"]["is_hidden_fact"] is True
        assert by_name["Sentiment"]["include"] is True
        assert by_name["Sentiment"]["is_hidden_fact"] is True
        assert by_name["DateTableTemplate_1"]["include"] is False
        assert by_name["LocalDateTable_1"]["include"] is False

        model = TMDLToOSIConverter().to_osi(
                {
                        "tmdl_files": tmdl_files,
                        "workspace_id": "workspace-1",
                        "dataset_id": "dataset-1",
                        "display_name": "DemoModel",
                }
        )
        dataset_names = {dataset.unique_name for dataset in model.datasets}

        assert "SalesFact" in dataset_names
        assert "Sentiment" in dataset_names
        assert "DateTableTemplate_1" not in dataset_names
        assert "LocalDateTable_1" not in dataset_names


def test_relationships_are_preserved_and_date_variations_are_skipped() -> None:
        tmdl_files = _build_tmdl_files()
        model = TMDLToOSIConverter().to_osi(
                {
                        "tmdl_files": tmdl_files,
                        "workspace_id": "workspace-1",
                        "dataset_id": "dataset-1",
                        "display_name": "DemoModel",
                }
        )

        relationship_pairs = {
                (rel.from_dataset, rel.to_dataset)
                for rel in model.relationships
        }

        assert ("SalesFact", "Product") in relationship_pairs
        assert ("SalesFact", "Date") in relationship_pairs
        assert ("SalesFact", "Geo") in relationship_pairs
        assert ("Sentiment", "Geo") in relationship_pairs
        assert ("Sentiment", "Date") in relationship_pairs
        assert ("Sentiment", "Manufacturer") in relationship_pairs
        assert len(model.relationships) >= 8


def test_measures_resolve_without_null_emission_and_cross_table_refs_work() -> None:
        gross_margin = SimpleNamespace(
                unique_name="GrossMargin",
                dataset="SalesFact",
                expression="[Sales] - SUM(SalesFact[Cost])",
                sql_expression='SUM(SALESFACT."REVENUE") - SUM(SALESFACT."COST")',
        )
        sales = SimpleNamespace(
                unique_name="Sales",
                dataset="SalesFact",
                expression="SUM(SalesFact[Revenue])",
                sql_expression='SUM(SALESFACT."REVENUE")',
        )
        margin_pct = SimpleNamespace(
                unique_name="GrossMarginPct",
                dataset="KPI",
                expression="DIVIDE([GrossMargin], [Sales])",
                sql_expression=None,
        )

        translator = DAXTranslator()
        translator.osi_model = SimpleNamespace(metrics=[gross_margin, sales, margin_pct])

        resolved = translator.translate_expr(margin_pct.expression, [gross_margin, sales, margin_pct], "KPI")

        assert resolved is not None
        assert " AS NULL" not in resolved.upper()
        assert "[" not in resolved and "]" not in resolved
        assert 'SUM(SALESFACT."REVENUE") - SUM(SALESFACT."COST")' in resolved
        assert 'SUM(SALESFACT."REVENUE")' in resolved

        sanitizer = SemanticDDLSanitizer(DummyIdentifierSanitizer())
        sanitized = sanitizer.sanitize_structure(
                """
CREATE OR REPLACE SEMANTIC VIEW DEMO
METRICS (
)
;
""".strip()
        )

        assert "AS NULL" not in sanitized.upper()
        assert "COUNT(1)" in sanitized.upper()
