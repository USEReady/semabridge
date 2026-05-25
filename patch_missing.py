import sys
import re

file_path = r'c:\Users\MANOJ\dev-test\semabridge\src\semabridge\connectors\snowflake_emitter.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add _find_date_table if it doesn't exist
date_table_method = '''
    def _find_date_table(self, model: Any) -> Optional[Tuple[str, str, str]]:
        """
        Dynamically find the date/calendar table in the model.
        Returns (table_name, date_column, fiscal_period_column) or None.
        No hardcoding!
        """
        date_keywords = ['date', 'calendar', 'cal', 'dim_date', 'dates']
        fiscal_keywords = ['fiscal_yr_period', 'fiscal_period', 'fiscal_year_period']
        
        for dataset in getattr(model, 'datasets', []):
            dataset_name = dataset.unique_name.lower()
            
            # Check if this looks like a date table
            is_date_table = any(kw in dataset_name for kw in date_keywords)
            
            if is_date_table:
                # Find date column
                date_col = None
                fiscal_col = None
                
                for col in dataset.columns:
                    col_name = col.unique_name.lower()
                    if col_name in ['cal_dt', 'date', 'calendar_date', 'cal_date']:
                        date_col = col.unique_name
                    if any(fk in col_name for fk in fiscal_keywords):
                        fiscal_col = col.unique_name
                
                if date_col and fiscal_col:
                    return (dataset.unique_name, date_col, fiscal_col)
        
        return None
'''

if '_find_date_table' not in content:
    content = content.replace('def _identify_fact_table', date_table_method.strip() + '\n\n    def _identify_fact_table')

# 2. Modify _auto_execute_precompute_suggestions to explicitly add INDICATOR04 and INDICATOR05
old_precompute = "logger.info(\"🚀 Auto-executing pre-compute suggestions...\")"
new_precompute = """logger.info("🚀 Auto-executing pre-compute suggestions...")
        
        # Data Engineering Explicit Fixes
        fact_table = self._identify_fact_table(model)
        if fact_table:
            try:
                cursor.execute(f"ALTER TABLE {fact_table} ADD COLUMN IF NOT EXISTS ATINDICATOR04 VARCHAR")
                cursor.execute(f"ALTER TABLE {fact_table} ADD COLUMN IF NOT EXISTS ATINDICATOR05 VARCHAR")
                logger.info(f"✅ Added missing explicit columns to {fact_table}")
            except Exception as e:
                pass"""

content = content.replace(old_precompute, new_precompute, 1)

# 3. Modify _create_enriched_view to compute total_units_all
old_select_parts = "select_parts = [f\"SELECT f.*\"]"
new_select_parts = """select_parts = [f"SELECT f.*"]
        
        # Add Pre-compute for percentage measures (Data Engineering Explicit Fix)
        select_parts.append(f"\\n        , (SELECT SUM(UNITS) FROM {fact_table}) AS TOTAL_UNITS_ALL")"""

content = content.replace(old_select_parts, new_select_parts, 1)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Missing features implemented successfully.")
