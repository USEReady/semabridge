import sys
sys.path.append("src")
from semabridge.connectors.translator import MetricExpressionTranslator
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
from unittest.mock import Mock
import logging

def main():
    logging.basicConfig(level=logging.WARNING)
    id_sanitizer = IdentifierSanitizer()
    translator = MetricExpressionTranslator(id_sanitizer)
    
    config = Mock()
    
    builder = MetricsClauseBuilder(
        identifier_sanitizer=id_sanitizer,
        schema_manager=Mock(),
        sanitizer=Mock(),
        translator=translator,
        config=config,
        dup_name_repo=Mock()
    )
    
    dataset_col_lookup = {
        "SalesFact": {"UNITS", "PRODUCT_ISVANARSDEL"},
        "Product": {"ISVANARSDEL"}
    }
    dataset_aliases = {"SalesFact": "SALESFACT", "Product": "PRODUCT"}
    
    # Simulate what dax_rule_translator produces
    sql = 'SUM(CASE WHEN PRODUCT."ISVANARSDEL" = \'Yes\' THEN SALESFACT."UNITS" ELSE 0 END)'
    
    sql2 = builder._rewrite_cross_dataset_sql_refs_to_precomputed(
        sql_expr=sql,
        active_dataset="SalesFact",
        active_alias="SALESFACT",
        dataset_aliases=dataset_aliases,
        dataset_col_lookup=dataset_col_lookup
    )
    print("1.", sql2)

    sql3 = translator.fix_common_llm_issues(sql2, "...", dataset_aliases, dataset_col_lookup)
    print("2.", sql3)

    sql4 = translator._normalize_metric_column_references(
        sql3,
        "Metric1",
        dataset_col_lookup,
        dataset_aliases,
        {"Metric1"},
        preferred_table_alias="SALESFACT"
    )
    print("3.", sql4)

if __name__ == "__main__":
    main()
