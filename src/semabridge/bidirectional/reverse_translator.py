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
        
        # 1. Safe division generated from DAX DIVIDE.
        dax = self._translate_safe_division_to_dax(dax, table_name)

        # 2. Basic Aggregations (Simplified regex)
        dax = re.sub(r'SUM\s*\((.*?)\)', lambda m: f"SUM('{table_name}'[{self._sql_identifier_to_dax_column(m.group(1))}])", dax, flags=re.IGNORECASE)
        dax = re.sub(r'AVG\s*\((.*?)\)', lambda m: f"AVERAGE('{table_name}'[{self._sql_identifier_to_dax_column(m.group(1))}])", dax, flags=re.IGNORECASE)
        
        # 3. Cleanup (Remove SELECT and FROM parts)
        dax = re.sub(r'SELECT\s+', '', dax, flags=re.IGNORECASE)
        dax = re.sub(r'FROM\s+.*', '', dax, flags=re.IGNORECASE).strip()
        
        # 4. Cleanup double quotes
        dax = dax.replace('"', '')
        
        logger.info(f"Reverse translated: {sql} -> {dax}")
        return dax

    def _sql_identifier_to_dax_column(self, expression: str) -> str:
        """Convert a SQL column reference into a Fabric DAX column token."""
        value = str(expression or "").strip()
        value = re.sub(r"::\s*[A-Za-z0-9_]+", "", value)
        value = re.sub(r"\bTRY_CAST\s*\((.*)\s+AS\s+[A-Za-z0-9_()]+\s*\)", r"\1", value, flags=re.IGNORECASE)
        value = value.strip("() ")
        parts = re.findall(r'"([^"]+)"|`([^`]+)`|([A-Za-z_][A-Za-z0-9_$]*)', value)
        tokens = [next(item for item in part if item) for part in parts]
        if tokens:
            return tokens[-1]
        return value.replace("'", "").replace("`", "").replace('"', "")

    def _sql_scalar_to_dax(self, expression: str, table_name: str) -> str:
        value = str(expression or "").strip()
        while value.startswith("(") and value.endswith(")"):
            inner = value[1:-1].strip()
            if inner.count("(") != inner.count(")"):
                break
            value = inner

        aggregate = re.fullmatch(
            r"(?is)(SUM|AVG|AVERAGE|COUNT|MIN|MAX)\s*\(\s*(.*?)\s*\)",
            value,
        )
        if aggregate:
            func = aggregate.group(1).upper()
            if func == "AVG":
                func = "AVERAGE"
            col = self._sql_identifier_to_dax_column(aggregate.group(2))
            return f"{func}('{table_name}'[{col}])"

        if re.fullmatch(r"-?\d+(?:\.\d+)?|NULL", value, flags=re.IGNORECASE):
            return value.upper() if value.upper() == "NULL" else value

        col = self._sql_identifier_to_dax_column(value)
        return f"'{table_name}'[{col}]"

    def _translate_safe_division_to_dax(self, sql: str, table_name: str) -> str:
        """Convert Snowflake/Databricks safe division back to DAX DIVIDE."""
        text = str(sql or "")
        pattern = re.compile(
            r"(?is)COALESCE\s*\(\s*"
            r"(?P<num>.*?)\s*/\s*NULLIF\s*\(\s*(?P<den>.*?)\s*,\s*0\s*\)\s*,\s*"
            r"(?P<alt>[^()]+?)\s*\)"
        )

        def _replace(match: re.Match) -> str:
            numerator = self._sql_scalar_to_dax(match.group("num"), table_name)
            denominator = self._sql_scalar_to_dax(match.group("den"), table_name)
            alternate = match.group("alt").strip()
            return f"DIVIDE({numerator}, {denominator}, {alternate})"

        return pattern.sub(_replace, text)

    def infer_relationships(self, snowflake_fks: List[Dict]) -> List[str]:
        """
        Infers Fabric relationships from Snowflake Foreign Keys.
        """
        relationships = []
        for fk in snowflake_fks:
            rel = f"'{fk['table']}'[{fk['column']}] -> '{fk['ref_table']}'[{fk['ref_column']}]"
            relationships.append(rel)
        return relationships
