"""
Reverse Translator: Snowflake SQL to DAX.

Translates Snowflake SQL expressions back into DAX measures for 
bidirectional synchronization.
"""

import re
from typing import Optional, Dict, List
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

class ReverseTranslator:
    """
    Handles reverse translation from SQL back to DAX.
    """
    
    def __init__(self, table_mapping: Optional[Dict[str, str]] = None):
        self.table_mapping = table_mapping or {"fact": "Sales"}

    def translate_to_dax(self, sql: str, target_table: str = "Sales") -> Optional[str]:
        """
        Converts a Snowflake SQL expression to a DAX measure.
        """
        if not sql:
            return None
            
        dax = sql.strip()
        
        # Extract table from FROM clause if present
        table_match = re.search(r'FROM\s+([A-Za-z_][A-Za-z0-9_]*)', dax, re.IGNORECASE)
        table_name = table_match.group(1) if table_match else target_table
        
        # 1. Basic Aggregations (Simplified regex)
        dax = re.sub(r'SUM\s*\((.*?)\)', rf"SUM('{table_name}'[\1])", dax, flags=re.IGNORECASE)
        dax = re.sub(r'AVG\s*\((.*?)\)', rf"AVERAGE('{table_name}'[\1])", dax, flags=re.IGNORECASE)
        
        # 2. Cleanup (Remove SELECT and FROM parts)
        dax = re.sub(r'SELECT\s+', '', dax, flags=re.IGNORECASE)
        dax = re.sub(r'FROM\s+.*', '', dax, flags=re.IGNORECASE).strip()
        
        # 3. Cleanup double quotes
        dax = dax.replace('"', '')
        
        logger.info(f"Reverse translated: {sql} -> {dax}")
        return dax

    def infer_relationships(self, snowflake_fks: List[Dict]) -> List[str]:
        """
        Infers Fabric relationships from Snowflake Foreign Keys.
        """
        relationships = []
        for fk in snowflake_fks:
            rel = f"'{fk['table']}'[{fk['column']}] -> '{fk['ref_table']}'[{fk['ref_column']}]"
            relationships.append(rel)
        return relationships
