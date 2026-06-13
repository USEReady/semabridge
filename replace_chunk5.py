import pathlib
import re

path = pathlib.Path('src/semabridge/connectors/snowflake_emitter.py')
text = path.read_text(encoding='utf-8')

target = """    def _resolve_physical_col_name(self, dax_col_name: str, existing_cols: set[str]) -> str:
        candidates = [dax_col_name.upper(), f"COL_{dax_col_name.upper()}", dax_col_name.upper().replace(' ', '_')]
        for c in candidates:
            if c in existing_cols:
                return c
        for existing in existing_cols:
            if dax_col_name.upper() in existing:
                return existing
        return dax_col_name.upper().replace(' ', '_')"""

replacement = """    def _resolve_physical_col_name(self, dax_col_name: str, existing_cols: set[str]) -> str:
        patterns = getattr(self.behavior.snowflake.dynamic, 'physical_column_patterns', ["{name}", "COL_{name}", "{name}_ID", "{name}_KEY", "{name}_SK"])
        base_name = dax_col_name.upper()
        base_name_nospace = base_name.replace(' ', '_')
        
        candidates = []
        for p in patterns:
            candidates.append(p.replace("{name}", base_name).upper())
            if " " in base_name:
                candidates.append(p.replace("{name}", base_name_nospace).upper())
                
        for c in candidates:
            if c in existing_cols:
                return c
        for existing in existing_cols:
            if base_name in existing or base_name_nospace in existing:
                return existing
        return base_name_nospace"""

if target in text:
    text = text.replace(target, replacement)
    print('Replaced chunk 5')
else:
    print('Chunk 5 target not found')

path.write_text(text, encoding='utf-8')
