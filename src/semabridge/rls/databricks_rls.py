"""
Databricks Security Translator (Unity Catalog).

Translates Fabric RLS policies into Databricks Unity Catalog 
Row Filters and Column Masks.
"""

from typing import List, Optional
from semabridge.models.phase_3_enterprise import RLSPolicy
from semabridge.converter.dax_engine import get_translation_engine
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

class DatabricksRlsTranslator:
    """
    Translates RLS policies to Databricks (Spark SQL) DDL.
    """
    
    def __init__(self):
        self.engine = get_translation_engine(target_dialect="databricks")

    def translate_to_databricks(self, policies: List[RLSPolicy]) -> List[str]:
        """
        Generates Databricks Unity Catalog DDL for RLS policies.
        """
        ddl_statements = []
        
        for policy in policies:
            # Databricks Unity Catalog uses SQL Functions for filters
            func_name = f"{policy.table}_filter_func".lower()
            sql_filter, _ = self.engine.translate(policy.filter_expression)
            
            ddl = f"""
-- 1. Create Filter Function
CREATE OR REPLACE FUNCTION {func_name}(region_col STRING)
RETURN IS_ACCOUNT_GROUP_MEMBER('{policy.role}') AND {sql_filter};

-- 2. Apply to Table
ALTER TABLE {policy.table} SET ROW FILTER {func_name} ON (region);
"""
            ddl_statements.append(ddl.strip())
            
        return ddl_statements
