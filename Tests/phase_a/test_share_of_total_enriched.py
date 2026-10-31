"""
Tests for Feature: "Share of Total" (% of Grand Total) measures using Snowflake
native Semantic View window metrics: SUM(...) OVER (PARTITION BY EXCLUDING <dim>) (Approach B1).

Validates:
1. DAX with ALL(table) or ALL(table[column]) translates natively to SQL with PARTITION BY EXCLUDING.
2. Direct translation and dependent measure translation succeed out of the box without environment flags.
3. Cases % by Product and Cases % by Subject translate and emit into Snowflake Semantic View DDL cleanly.
4. No helper view (_ENRICHED) or temporary precomputed columns are generated.
5. Selective dimension filters and unsupported patterns (ALLSELECTED, complex filters) stay cleanly as NULL placeholders.
"""
import os
import sys
import unittest
from types import SimpleNamespace

PHASE_A_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(PHASE_A_DIR, "../.."))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))
sys.path.insert(0, os.path.join(PHASE_A_DIR, "feature_tests_for_measures"))

from semabridge.converter.dax_translator import DAXTranslator
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.sml.models import SMLModel, SMLDataset, SMLColumn, SMLMetric, SMLRelationship
from fixture import TABLES, RELATIONSHIPS


class TestShareOfTotalNativeWindow(unittest.TestCase):
    def setUp(self):
        self.translator = DAXTranslator()
        self.orders = TABLES["Orders"].copy()
        self.product = TABLES["Product"].copy()
        self.datasets = [
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
        ]

    def test_direct_share_of_total_translation(self):
        """DIVIDE(SUM(Orders[Amount]), CALCULATE(SUM(Orders[Amount]), ALL(Product)))
        translates to native Snowflake window metric with PARTITION BY EXCLUDING.
        """
        dax = "DIVIDE(SUM(Orders[Amount]), CALCULATE(SUM(Orders[Amount]), ALL(Product)))"
        res = self.translator.translate(
            dax, "orders", "Orders", "Share of Total",
            datasets=self.datasets, skip_tier5=True
        )
        self.assertTrue(res.is_success, f"Translation failed: {res.failure_reason}")
        self.assertIn("PARTITION BY EXCLUDING", res.sql)
        self.assertIn("NULLIF", res.sql)

    def test_dependent_measure_share_of_total_translation(self):
        """DIVIDE([Total Amount], CALCULATE([Total Amount], ALL(Product)))
        translates to native Snowflake window metric with PARTITION BY EXCLUDING.
        """
        dax = "DIVIDE([Total Amount], CALCULATE([Total Amount], ALL(Product)))"
        ctx = [
            SimpleNamespace(
                unique_name="Total Amount",
                name="Total Amount",
                expression="SUM(Orders[Amount])",
                dataset="Orders",
                sql_expression="SUM(orders.\"AMOUNT\")",
                sync_enabled=True,
            )
        ]

        res = self.translator.translate(
            dax, "orders", "Orders", "Share of Category",
            datasets=self.datasets, metrics_context=ctx, skip_tier5=True
        )
        self.assertTrue(res.is_success, f"Translation failed: {res.failure_reason}")
        self.assertIn("PARTITION BY EXCLUDING", res.sql)

    def test_cases_percentage_by_product_and_subject(self):
        """User test measures from AI Sample:
        - Cases % by Product = DIVIDE([Case Count], CALCULATE([Case Count], All('Products')))
        - Cases % by Subject = DIVIDE([Case Count], CALCULATE([Case Count], All('Subjects')))
        """
        cases_dataset = SMLDataset(
            unique_name="Cases",
            columns=[
                SMLColumn(unique_name="CaseId", data_type="integer"),
                SMLColumn(unique_name="ProductId", data_type="integer"),
                SMLColumn(unique_name="SubjectId", data_type="integer"),
                SMLColumn(unique_name="Subject", data_type="string"),
            ],
        )
        products_dataset = SMLDataset(
            unique_name="Products",
            columns=[
                SMLColumn(unique_name="ProductId", data_type="integer"),
                SMLColumn(unique_name="Product", data_type="string"),
            ],
        )
        datasets = [cases_dataset, products_dataset]

        ctx = [
            SimpleNamespace(
                unique_name="Case Count",
                name="Case Count",
                expression="COUNTROWS('Cases')",
                dataset="Cases",
                sql_expression="COUNT(*)",
                sync_enabled=True,
            )
        ]

        dax_prod = "DIVIDE([Case Count], CALCULATE([Case Count], All('Products')))"
        res_prod = self.translator.translate(
            dax_prod, "cases", "Cases", "Cases % by Product",
            datasets=datasets, metrics_context=ctx, skip_tier5=True
        )
        self.assertTrue(res_prod.is_success, f"Failed: {res_prod.failure_reason}")
        self.assertIn("PARTITION BY EXCLUDING", res_prod.sql)
        self.assertIn("PRODUCTS", res_prod.sql.upper())

        dax_subj = "DIVIDE([Case Count], CALCULATE([Case Count], All('Cases'[Subject])))"
        res_subj = self.translator.translate(
            dax_subj, "cases", "Cases", "Cases % by Subject",
            datasets=datasets, metrics_context=ctx, skip_tier5=True
        )
        self.assertTrue(res_subj.is_success, f"Failed: {res_subj.failure_reason}")
        self.assertIn("PARTITION BY EXCLUDING", res_subj.sql)
        self.assertIn("CASES", res_subj.sql)
        self.assertIn("SUBJECT", res_subj.sql)

    def test_selective_dimension_and_unsupported_patterns_stay_null(self):
        """Verify Group 2 and Group 3 (CSAT Impact, ALLSELECTED)
        stay safely as not_applicable / NULL placeholders rather than computing incorrect numbers.
        """
        # Group 3: ALLSELECTED (must not be treated as ALL)
        dax_allselected = "CALCULATE(SUM(Sales[Units]), ALLSELECTED(Product))"
        res_as = self.translator.translate(
            dax_allselected, "sales", "Sales", "WIF Units Returned",
            skip_tier5=True
        )
        self.assertFalse(res_as.is_success)

        # Group 3: ALL + SELECTEDVALUE (CSAT Impact)
        dax_csat = "CALCULATE(COUNT(Cases[CaseId]), ALL(Cases), Cases[Type] = SELECTEDVALUE(Cases[Type]))"
        res_csat = self.translator.translate(
            dax_csat, "cases", "Cases", "CSAT Impact",
            skip_tier5=True
        )
        self.assertFalse(res_csat.is_success)

    def test_snowflake_ddl_no_helper_view_needed(self):
        """Verify that SnowflakeEmitter.generate_ddls produces clean Semantic View DDL
        containing the native window metrics and NO helper _ENRICHED view.
        """
        model = SMLModel(
            unique_name="test_ai_model",
            datasets=[
                SMLDataset(
                    unique_name="Cases",
                    columns=[
                        SMLColumn(unique_name="CaseId", data_type="integer"),
                        SMLColumn(unique_name="ProductId", data_type="integer"),
                        SMLColumn(unique_name="Subject", data_type="string"),
                    ],
                ),
                SMLDataset(
                    unique_name="Products",
                    columns=[
                        SMLColumn(unique_name="ProductId", data_type="integer"),
                        SMLColumn(unique_name="Product", data_type="string"),
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
                    sql_expression='CASES."CASE_COUNT" / NULLIF(SUM(CASES."CASE_COUNT") OVER (PARTITION BY EXCLUDING PRODUCTS.PRODUCT), 0)',
                ),
            ],
            relationships=[
                SMLRelationship(
                    unique_name="Cases_Products_ProductId",
                    from_dataset="Cases",
                    from_columns=["ProductId"],
                    to_dataset="Products",
                    to_columns=["ProductId"],
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
        ddls = emitter.generate_ddls(model)

        full_ddl = "\n".join(ddls)
        self.assertIn("SEMANTIC VIEW", full_ddl)
        self.assertIn("PARTITION BY EXCLUDING", full_ddl)
        # Verify no _ENRICHED view or helper view is generated
        self.assertNotIn("_ENRICHED", full_ddl)
        self.assertNotIn("ALL_COUNT", full_ddl)


if __name__ == "__main__":
    unittest.main()
