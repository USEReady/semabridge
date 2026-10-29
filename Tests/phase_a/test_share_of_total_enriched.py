"""
Tests for Feature: "Share of Total" (% of Grand Total) measures using enriched helper view.

Validates:
1. DAX with ALL(table) or ALL(table[column]) translates to SQL referencing helper view window columns (e.g. ALL_AMOUNT)
2. Enriched view column generation: SUM(sales) OVER () AS ALL_AMOUNT
3. Execution in local DuckDB returns exact mathematical shares:
   - Grand total = 1.0 (100%)
   - Grouped by dimension (Category) sums to 1.0
4. Preserves backward compatibility: without allow_window_enrichment, returns 'not applicable: needs window metric'
"""
import os
import sys
import unittest
import duckdb
import pandas as pd

# Add source and test paths
PHASE_A_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(PHASE_A_DIR, "../.."))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
sys.path.insert(0, os.path.join(PHASE_A_DIR, "feature_tests_for_measures"))

from semabridge.converter.dax_translator import DAXTranslator
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.sml.models import SMLModel, SMLDataset, SMLColumn, SMLMetric, SMLRelationship
from fixture import TABLES, RELATIONSHIPS


class TestShareOfTotalEnriched(unittest.TestCase):
    def setUp(self):
        self.translator = DAXTranslator()
        self.orders = TABLES["Orders"].copy()
        self.product = TABLES["Product"].copy()

    def test_direct_share_of_total_translation(self):
        """F14.1 pattern: DIVIDE(SUM(Orders[Amount]), CALCULATE(SUM(Orders[Amount]), ALL(Product)))"""
        dax = "DIVIDE(SUM(Orders[Amount]), CALCULATE(SUM(Orders[Amount]), ALL(Product)))"

        # 1. Default translation without enrichment should decline cleanly (Phase A F14 baseline)
        res_default = self.translator.translate(dax, "orders", "Orders", "Share of Total", skip_tier5=True)
        self.assertFalse(res_default.is_success)
        self.assertEqual(res_default.failure_reason, "not applicable: needs window metric")

        # 2. Translation with allow_window_enrichment should succeed
        res_enriched = self.translator.translate(
            dax, "orders", "Orders", "Share of Total",
            skip_tier5=True, allow_window_enrichment=True
        )
        self.assertTrue(res_enriched.is_success, f"Translation failed: {res_enriched.failure_reason}")
        self.assertIn("AMOUNT", res_enriched.sql)
        self.assertIn("ALL_AMOUNT", res_enriched.sql)
        self.assertIn("NULLIF", res_enriched.sql)

    def test_dependent_measure_share_of_total_translation(self):
        """F14.2 pattern: DIVIDE([Total Amount], CALCULATE([Total Amount], ALL(Product)))"""
        dax = "DIVIDE([Total Amount], CALCULATE([Total Amount], ALL(Product)))"
        base_res = self.translator.translate("SUM(Orders[Amount])", "orders", "Orders", "Total Amount", skip_tier5=True)

        from types import SimpleNamespace
        ctx = [
            SimpleNamespace(
                unique_name="Total Amount",
                name="Total Amount",
                expression="SUM(Orders[Amount])",
                dataset="Orders",
                sql_expression=base_res.sql,
                sync_enabled=True,
            )
        ]

        res_enriched = self.translator.translate(
            dax, "orders", "Orders", "Share of Category",
            metrics_context=ctx, skip_tier5=True, allow_window_enrichment=True
        )
        self.assertTrue(res_enriched.is_success, f"Translation failed: {res_enriched.failure_reason}")
        self.assertIn("ALL_AMOUNT", res_enriched.sql)

    def test_enriched_view_column_discovery(self):
        """SnowflakeEmitter._build_window_precomputed_columns generates SUM(f.AMOUNT) OVER () AS ALL_AMOUNT"""
        model = SMLModel(
            unique_name="test_model",
            datasets=[
                SMLDataset(
                    unique_name="Orders",
                    columns=[
                        SMLColumn(unique_name="OrderID", data_type="integer"),
                        SMLColumn(unique_name="ProductID", data_type="integer"),
                        SMLColumn(unique_name="Amount", data_type="float"),
                    ],
                ),
                SMLDataset(
                    unique_name="Product",
                    columns=[
                        SMLColumn(unique_name="ProductID", data_type="integer"),
                        SMLColumn(unique_name="Category", data_type="string"),
                    ],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Share of Total",
                    dataset="Orders",
                    expression="DIVIDE(SUM(Orders[Amount]), CALCULATE(SUM(Orders[Amount]), ALL(Product)))",
                )
            ],
            relationships=[
                SMLRelationship(
                    unique_name="Orders_Product_ProductID",
                    from_dataset="Orders",
                    from_columns=["ProductID"],
                    to_dataset="Product",
                    to_columns=["ProductID"],
                    is_active=True,
                )
            ],
        )

        from semabridge.core.settings import SnowflakeConfig
        config = SnowflakeConfig(
            account="test", user="test", password="test",
            database="TEST_DB", schema_name="PUBLIC", warehouse="TEST_WH"
        )
        emitter = SnowflakeEmitter(config)
        projected = set()
        window_cols = emitter._build_window_precomputed_columns(model, "Orders", projected)

        self.assertTrue(len(window_cols) > 0, "No window columns generated")
        col_sql = window_cols[0]
        self.assertIn("SUM(f.\"AMOUNT\") OVER ()", col_sql)
        self.assertIn('"ALL_AMOUNT"', col_sql)

    def test_duckdb_execution_local(self):
        """Execute translated SQL on local DuckDB against enriched view and verify math."""
        dax = "DIVIDE(SUM(Orders[Amount]), CALCULATE(SUM(Orders[Amount]), ALL(Product)))"
        res = self.translator.translate(dax, "orders", "Orders", "Share of Total", skip_tier5=True, allow_window_enrichment=True)
        self.assertTrue(res.is_success)

        con = duckdb.connect()
        con.register("raw_orders", self.orders)
        con.register("raw_product", self.product)

        # Build enriched view simulating _create_enriched_view
        con.execute("""
            CREATE TABLE orders AS
            SELECT
                f.*,
                SUM(f.Amount) OVER () AS "ALL_AMOUNT"
            FROM raw_orders f
        """)
        con.execute('CREATE TABLE product AS SELECT * FROM raw_product')

        # 1. Grand total calculation
        total_sql = f"SELECT {res.sql} AS share FROM orders"
        total_share = con.execute(total_sql).fetchone()[0]
        self.assertAlmostEqual(total_share, 1.0, places=5)

        # 2. Dimension breakdown (by Product.Category)
        cat_sql = f"""
            SELECT
                p.Category,
                {res.sql} AS share
            FROM orders
            JOIN product p ON orders.ProductID = p.ProductID
            GROUP BY p.Category
            ORDER BY p.Category
        """
        rows = con.execute(cat_sql).fetchall()
        self.assertEqual(len(rows), 2)
        shares_by_cat = {cat: val for cat, val in rows}

        total_amount = self.orders["Amount"].sum()
        hardware_amount = self.orders[self.orders["ProductID"] == 1]["Amount"].sum()
        software_amount = self.orders[self.orders["ProductID"].isin([2, 3])]["Amount"].sum()

        expected_hw = hardware_amount / total_amount
        expected_sw = software_amount / total_amount

        self.assertAlmostEqual(shares_by_cat["Hardware"], expected_hw, places=5)
        self.assertAlmostEqual(shares_by_cat["Software"], expected_sw, places=5)
        self.assertAlmostEqual(sum(shares_by_cat.values()), 1.0, places=5)


    def test_cases_percentage_by_product(self):
        """User test measure:
        MEASURE 'Cases'[Cases % by Product] = DIVIDE([Case Count], CALCULATE([Case Count], All('Products')))
        Supports both COUNTROWS('Cases') and COUNT(Cases[CaseId]).
        """
        from types import SimpleNamespace

        dax = """DIVIDE(
            [Case Count],
            CALCULATE(
                [Case Count],All('Products')
            )
        )"""

        # 1. With COUNTROWS('Cases')
        ctx_countrows = [
            SimpleNamespace(
                unique_name="Case Count",
                name="Case Count",
                expression="COUNTROWS('Cases')",
                dataset="Cases",
                sql_expression="COUNT(*)",
                sync_enabled=True,
            )
        ]
        res_cr = self.translator.translate(
            dax, "cases", "Cases", "Cases % by Product",
            metrics_context=ctx_countrows, skip_tier5=True, allow_window_enrichment=True
        )
        self.assertTrue(res_cr.is_success, f"Failed: {res_cr.failure_reason}")
        self.assertIn("ALL_COUNT", res_cr.sql)

        # 2. With COUNT(Cases[CaseId])
        ctx_count = [
            SimpleNamespace(
                unique_name="Case Count",
                name="Case Count",
                expression="COUNT(Cases[CaseId])",
                dataset="Cases",
                sql_expression="COUNT(cases.\"CASEID\")",
                sync_enabled=True,
            )
        ]
        res_c = self.translator.translate(
            dax, "cases", "Cases", "Cases % by Product",
            metrics_context=ctx_count, skip_tier5=True, allow_window_enrichment=True
        )
        self.assertTrue(res_c.is_success, f"Failed: {res_c.failure_reason}")
        self.assertIn("ALL_CASEID", res_c.sql)

    def test_selective_dimension_and_unsupported_patterns_stay_null(self):
        """Verify Group 2 (Life Expectancy) and Group 3 (CSAT Impact, ALLSELECTED)
        stay safely as not_applicable / NULL placeholders rather than computing incorrect numbers.
        """
        # Group 2: Selective dimension windowing ALL('Fact'[Year])
        dax_gdp = "CALCULATE(AVERAGE(Fact[GDP]), ALL('Fact'[Year]))"
        res_gdp = self.translator.translate(
            dax_gdp, "fact", "Fact", "Life Expectancy GDP",
            skip_tier5=True, allow_window_enrichment=True
        )
        self.assertFalse(res_gdp.is_success)
        self.assertEqual(res_gdp.failure_reason, "not applicable: needs window metric")

        # Group 3: ALLSELECTED (must not be treated as ALL)
        dax_allselected = "CALCULATE(SUM(Sales[Units]), ALLSELECTED(Product))"
        res_as = self.translator.translate(
            dax_allselected, "sales", "Sales", "WIF Units Returned",
            skip_tier5=True, allow_window_enrichment=True
        )
        self.assertFalse(res_as.is_success)

        # Group 3: ALL + SELECTEDVALUE (CSAT Impact)
        dax_csat = "CALCULATE(COUNT(Cases[CaseId]), ALL(Cases), Cases[Type] = SELECTEDVALUE(Cases[Type]))"
        res_csat = self.translator.translate(
            dax_csat, "cases", "Cases", "CSAT Impact",
            skip_tier5=True, allow_window_enrichment=True
        )
        self.assertFalse(res_csat.is_success)

    def test_snowflake_emitter_window_rerender_end_to_end(self):
        """Verify that SnowflakeEmitter._rerender_window_enriched_metrics correctly
        upgrades whole-table share-of-total measures while leaving selective/complex measures untouched.
        """
        from semabridge.sml.models import SMLModel, SMLDataset, SMLColumn, SMLMetric
        from semabridge.core.settings import SnowflakeConfig

        model = SMLModel(
            unique_name="test_ai_model",
            datasets=[
                SMLDataset(
                    unique_name="Cases",
                    columns=[
                        SMLColumn(unique_name="CaseId", data_type="integer"),
                        SMLColumn(unique_name="ProductId", data_type="integer"),
                        SMLColumn(unique_name="SubjectId", data_type="integer"),
                        SMLColumn(unique_name="Type", data_type="string"),
                    ],
                ),
                SMLDataset(
                    unique_name="Products",
                    columns=[
                        SMLColumn(unique_name="ProductId", data_type="integer"),
                        SMLColumn(unique_name="ProductName", data_type="string"),
                    ],
                ),
                SMLDataset(
                    unique_name="Subjects",
                    columns=[
                        SMLColumn(unique_name="SubjectId", data_type="integer"),
                        SMLColumn(unique_name="SubjectName", data_type="string"),
                    ],
                ),
                SMLDataset(
                    unique_name="Fact",
                    columns=[
                        SMLColumn(unique_name="GDP", data_type="float"),
                        SMLColumn(unique_name="Year", data_type="integer"),
                    ],
                ),
            ],
            metrics=[
                SMLMetric(
                    unique_name="Case Count",
                    dataset="Cases",
                    expression="COUNTROWS('Cases')",
                    sql_expression="COUNT(*)",
                ),
                SMLMetric(
                    unique_name="Cases % by Product",
                    dataset="Cases",
                    expression="DIVIDE([Case Count], CALCULATE([Case Count], All('Products')))",
                ),
                SMLMetric(
                    unique_name="Cases % by Subject",
                    dataset="Cases",
                    expression="DIVIDE([Case Count], CALCULATE([Case Count], All('Subjects')))",
                ),
                SMLMetric(
                    unique_name="CSAT Impact",
                    dataset="Cases",
                    expression="CALCULATE([Case Count], ALL(Cases), Cases[Type] = SELECTEDVALUE(Cases[Type]))",
                ),
                SMLMetric(
                    unique_name="Life Expectancy GDP",
                    dataset="Fact",
                    expression="CALCULATE(AVERAGE(Fact[GDP]), ALL('Fact'[Year]))",
                ),
            ],
        )

        config = SnowflakeConfig(
            account="test", user="test", password="test",
            database="TEST_DB", schema_name="PUBLIC", warehouse="TEST_WH"
        )
        emitter = SnowflakeEmitter(config)
        lookup, aliases = self.translator.build_schema_lookup(model.datasets)

        rerendered = emitter._rerender_window_enriched_metrics(model, lookup, aliases)
        self.assertEqual(rerendered, 2, "Expected exactly 2 measures (Product & Subject shares) to be rerendered")

        # Verify Cases % by Product & Subject have valid SQL referencing ALL_COUNT
        prod_m = next(m for m in model.metrics if m.unique_name == "Cases % by Product")
        self.assertTrue(prod_m.sync_enabled)
        self.assertIn("ALL_COUNT", prod_m.sql_expression)

        subj_m = next(m for m in model.metrics if m.unique_name == "Cases % by Subject")
        self.assertTrue(subj_m.sync_enabled)
        self.assertIn("ALL_COUNT", subj_m.sql_expression)

        # Verify CSAT Impact and Life Expectancy remain None (will be emitted as NULL placeholders)
        csat_m = next(m for m in model.metrics if m.unique_name == "CSAT Impact")
        self.assertIsNone(csat_m.sql_expression)

        gdp_m = next(m for m in model.metrics if m.unique_name == "Life Expectancy GDP")
        self.assertIsNone(gdp_m.sql_expression)

if __name__ == "__main__":
    unittest.main()
