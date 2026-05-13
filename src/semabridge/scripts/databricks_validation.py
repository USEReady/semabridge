"""
Databricks Integration Validation.

Validates Spark SQL generation and Unity Catalog RLS DDL.
"""

from semabridge.rls.databricks_rls import DatabricksRlsTranslator
from semabridge.models.phase_3_enterprise import RLSPolicy
from semabridge.converter.dax_engine import get_translation_engine

def test_databricks_flow():
    print("=== SemaBridge: Databricks Integration Validation ===\n")
    
    # 1. Test SQL Translation (Databricks Dialect)
    engine = get_translation_engine()
    dax = "SUMX('Sales', 'Sales'[Amount] * 1.1)"
    sql, _ = engine.translate(dax, target_dialect="databricks")
    
    print(f"DAX: {dax}")
    print(f"Databricks SQL: {sql}")
    print("-" * 40)
    
    # 2. Test RLS Translation (Unity Catalog)
    policy = RLSPolicy(
        id="d_test", 
        role="Finance_Team", 
        table="Sales", 
        filter_expression="'Sales'[Amount] > 100"
    )
    
    translator = DatabricksRlsTranslator()
    ddls = translator.translate_to_databricks([policy])
    
    print(f"RLS Filter: {policy.filter_expression}")
    print("Databricks Unity Catalog DDL:")
    print(ddls[0])
    print("-" * 40)

if __name__ == "__main__":
    test_databricks_flow()
