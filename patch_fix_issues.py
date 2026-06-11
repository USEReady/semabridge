import re

file_path = r'c:\Users\MANOJ\dev-test\semabridge\src\semabridge\connectors\translator.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# Replace fix_common_llm_issues
old_fix = '''    @staticmethod
    def fix_common_llm_issues(sql: str, dax: str = "") -> str:
        if not sql: return sql
        sql = sql.replace("CURRENT_DATE()", "MAX_DATE")
        sql = sql.replace("CURRENT_DATE", "MAX_DATE")
        sql = re.sub(r"salesfact\.", "SALESFACT.", sql, flags=re.IGNORECASE)

        # Normalize common LLM date-table alias errors: CALENDAR -> COL_DATE
        sql = re.sub(r'\bCALENDAR\.', 'COL_DATE.', sql, flags=re.IGNORECASE)
        # Normalize DATES alias (common LLM error) -> COL_DATE
        sql = re.sub(r'\bDATES\.', 'COL_DATE.', sql, flags=re.IGNORECASE)
        # Normalize DATE alias (common LLM error) -> COL_DATE
        sql = re.sub(r'\bDATE\.', 'COL_DATE.', sql, flags=re.IGNORECASE)
        # Remove bare ALIAS placeholders that LLMs occasionally emit
        sql = re.sub(r'\bALIAS\."?[A-Z_][A-Z0-9_]*"?', '', sql, flags=re.IGNORECASE).strip()

        # Test compliance overrides for LLM flakiness
        dax_upper = dax.upper()
        if "TOTALYTD" in dax_upper and "MAX_DATE" not in sql.upper() and "SUM" in sql.upper():
            return "SUM(CASE WHEN COL_DATE.\"YEAR\" = YEAR(MAX_DATE) AND COL_DATE.\"COL_DATE\" <= MAX_DATE THEN SALESFACT.UNITS ELSE 0 END)"

        if "DIVIDE" in dax_upper and "VANARSDEL" in dax_upper and "COALESCE" not in sql.upper():
            return "COALESCE(SUM(CASE WHEN SALESFACT.ISVANARSDEL THEN SALESFACT.UNITS ELSE 0 END) / NULLIF(SUM(SALESFACT.UNITS), 0), 0)"

        return sql'''

new_fix = '''    def _is_numeric_column(self, col_name: str) -> bool:
        """Check if column is numeric by name pattern."""
        numeric_patterns = ['UNITS', 'REVENUE', 'AMOUNT', 'PRICE', 'QTY', 'COUNT', 'SCORE', 'VALUE', 'COST', 'SALES', 'TOTAL', 'VOLUME']
        return any(pattern in col_name.upper() for pattern in numeric_patterns)

    def _find_fact_table_alias(self, dataset_aliases: Dict[str, str], dataset_col_lookup: Dict[str, set[str]]) -> Optional[str]:
        """Dynamically determine the fact table alias."""
        if not dataset_aliases or not dataset_col_lookup:
            return None
        for ds_name, alias in dataset_aliases.items():
            cols = dataset_col_lookup.get(ds_name, set())
            numeric_cols = sum(1 for col in cols if self._is_numeric_column(col))
            if numeric_cols > 3 or "FACT" in ds_name.upper():
                return alias
        return None

    def _find_date_column(self, dataset_col_lookup: Dict[str, set[str]]) -> str:
        """Find date column dynamically from schema."""
        if not dataset_col_lookup:
            return "COL_DATE"
        date_patterns = ['DATE', 'CAL_DATE', 'COL_DATE', 'TRANSACTION_DATE', 'ORDER_DATE', 'CREATED_DATE']
        for ds_name, cols in dataset_col_lookup.items():
            for col in cols:
                col_upper = col.upper()
                if any(pattern in col_upper for pattern in date_patterns):
                    return col
                if col_upper.endswith('_DATE') or col_upper.startswith('DATE_'):
                    return col
        return "COL_DATE"

    def _find_date_table_alias(self, dataset_aliases: Dict[str, str], dataset_col_lookup: Dict[str, set[str]]) -> Optional[str]:
        """Dynamically determine the date table alias."""
        if not dataset_aliases or not dataset_col_lookup:
            return None
        for ds_name, alias in dataset_aliases.items():
            cols = dataset_col_lookup.get(ds_name, set())
            for col in cols:
                col_upper = col.upper()
                if col_upper in ['DATE', 'CAL_DATE', 'COL_DATE']:
                    return alias
        return None

    def fix_common_llm_issues(self, sql: str, dax: str = "", dataset_aliases: Dict[str, str] = None, dataset_col_lookup: Dict[str, set[str]] = None) -> str:
        if not sql: return sql
        sql = sql.replace("CURRENT_DATE()", "MAX_DATE")
        sql = sql.replace("CURRENT_DATE", "MAX_DATE")

        # Dynamic fact table alias replacement
        fact_alias = self._find_fact_table_alias(dataset_aliases, dataset_col_lookup) if dataset_aliases and dataset_col_lookup else "SALESFACT"
        if fact_alias:
            sql = re.sub(r"salesfact\.", f"{fact_alias}.", sql, flags=re.IGNORECASE)

        # Dynamic date table alias replacement
        date_alias = self._find_date_table_alias(dataset_aliases, dataset_col_lookup) if dataset_aliases and dataset_col_lookup else "COL_DATE"
        if date_alias:
            date_aliases = ['calendar', 'dates', 'date', 'dim_date', 'cal']
            for d_alias in date_aliases:
                sql = re.sub(rf'\b{d_alias}\.', f'{date_alias}.', sql, flags=re.IGNORECASE)

        # Remove bare ALIAS placeholders that LLMs occasionally emit
        sql = re.sub(r'\bALIAS\."?[A-Z_][A-Z0-9_]*"?', '', sql, flags=re.IGNORECASE).strip()

        return sql'''

if old_fix in content:
    content = content.replace(old_fix, new_fix)
    
    # Also update calls to fix_common_llm_issues
    content = content.replace('self.fix_common_llm_issues(rule_based_sql, dax_expression)', 'self.fix_common_llm_issues(rule_based_sql, dax_expression, dataset_aliases, dataset_col_lookup)')
    content = content.replace('self.fix_common_llm_issues(expr, dax_expression)', 'self.fix_common_llm_issues(expr, dax_expression, dataset_aliases, dataset_col_lookup)')
    
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Patched fix_common_llm_issues successfully.")
else:
    print("Could not find fix_common_llm_issues.")
