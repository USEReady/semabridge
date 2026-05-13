"""
RLS Translation Engine for Semabridge.

Translates Fabric RLS (DAX filters) into Snowflake Row Access Policies (RAP)
and Dynamic Data Masking (DDM).
"""

from typing import List, Dict, Optional
from semabridge.converter.dax_ast_parser import DaxAstParser, DaxSqlRenderer

class RlsTranslator:
    """
    Translates DAX RLS rules to Snowflake SQL policies.
    """
    
    def __init__(self, table_alias: str = "T"):
        self.table_alias = table_alias
        self.parser = DaxAstParser()
        self.renderer = DaxSqlRenderer(table_alias=table_alias)

    def translate_rls_rule(self, dax_rule: str) -> str:
        """
        Translates a DAX RLS rule (e.g., 'User'[Email] = USERNAME())
        into a Snowflake Row Access Policy condition.
        """
        ast = self.parser.parse(dax_rule)
        if not ast:
            return "1=1"
            
        sql_condition = self.renderer.render(ast)
        
        # Snowflake specific mapping for USERNAME()
        if "USERNAME()" in dax_rule.upper():
            sql_condition = sql_condition.replace("USERNAME()", "CURRENT_USER()")
            
        return f"({sql_condition})"

    def generate_rap_ddl(self, policy_name: str, table_name: str, condition: str) -> str:
        """
        Generates Snowflake CREATE ROW ACCESS POLICY DDL.
        """
        return f"""
CREATE OR REPLACE ROW ACCESS POLICY {policy_name}
AS (val VARCHAR) RETURNS BOOLEAN ->
  EXISTS (
    SELECT 1 FROM {table_name}
    WHERE {condition}
  );
"""
