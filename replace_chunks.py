import pathlib
import re

path = pathlib.Path('src/semabridge/connectors/snowflake_emitter.py')
text = path.read_text(encoding='utf-8')

target1 = """        base_query_table = safe_table
        if base_query_table.upper().endswith("_ENRICHED"):
            base_query_table = base_query_table[:-9]
        elif base_query_table.upper().startswith("ENRICHED_"):
            base_query_table = base_query_table[9:]"""

replacement1 = """        base_query_table = safe_table
        patterns = getattr(self.behavior.snowflake.dynamic, 'enriched_view_patterns', ["{table}_ENRICHED", "ENRICHED_{table}", "{table}_VW", "VW_{table}"])
        for pattern in patterns:
            import re
            regex_str = '^' + re.escape(pattern).replace('\\\\{table\\\\}', '(.*)') + '$'
            m = re.match(regex_str, base_query_table, re.IGNORECASE)
            if m:
                base_query_table = m.group(1)
                break"""

if target1 in text:
    text = text.replace(target1, replacement1)
    print('Replaced chunk 3')
else:
    print('Chunk 3 target not found')

target2 = """                    else:
                        date_col_phys_raw = "Date"
                    if date_col_phys_raw:
                        date_col_phys = self._id.sanitize_column(date_col_phys_raw)
                    live_date_cols = {
                        str(col).strip('"').upper(): str(col).strip('"')
                        for col in (self._live_schema_metadata.get(str(date_table_name).upper(), set()) or set())
                    }
                    mapped_date_cols = {
                        str(col).strip('"').upper(): str(col).strip('"')
                        for col in (self._live_schema_metadata.get(str(self._id.sanitize_table_name(str(date_table_name or "DATE")).upper()).upper(), set()) or set())
                    }
                    available_date_cols = {**live_date_cols, **mapped_date_cols}
                    normalized_date_col = str(date_col_phys or "").strip('"').upper()
                    if normalized_date_col in available_date_cols:
                        date_col_phys = available_date_cols[normalized_date_col]
                    elif normalized_date_col in {"DATE", "CALENDAR_DATE"} and "COL_DATE" in available_date_cols:
                        date_col_phys = available_date_cols["COL_DATE"]
                if not date_col_phys:
                    date_col_phys = "COL_DATE" """

# There might be some trailing whitespace differences, so let's do regex replace if exact fails.
replacement2 = """                    else:
                        date_col_phys_raw = getattr(self.behavior.snowflake.dynamic, 'date_column', "COL_DATE")
                    if date_col_phys_raw:
                        date_col_phys = self._id.sanitize_column(date_col_phys_raw)
                    live_date_cols = {
                        str(col).strip('"').upper(): str(col).strip('"')
                        for col in (self._live_schema_metadata.get(str(date_table_name).upper(), set()) or set())
                    }
                    mapped_date_cols = {
                        str(col).strip('"').upper(): str(col).strip('"')
                        for col in (self._live_schema_metadata.get(str(self._id.sanitize_table_name(str(date_table_name or "DATE")).upper()).upper(), set()) or set())
                    }
                    available_date_cols = {**live_date_cols, **mapped_date_cols}
                    normalized_date_col = str(date_col_phys or "").strip('"').upper()
                    if normalized_date_col in available_date_cols:
                        date_col_phys = available_date_cols[normalized_date_col]
                    else:
                        date_column_patterns = [p.upper() for p in getattr(self.behavior.snowflake.dynamic, 'date_column_patterns', ["DATE", "CAL_DATE", "CALENDAR_DATE", "COL_DATE"])]
                        for fallback in date_column_patterns:
                            if fallback in available_date_cols:
                                date_col_phys = available_date_cols[fallback]
                                break
                if not date_col_phys:
                    date_col_phys = getattr(self.behavior.snowflake.dynamic, 'date_column', "COL_DATE")"""

if target2.strip() in text:
    text = text.replace(target2.strip(), replacement2)
    print('Replaced chunk 4')
else:
    # use regex to be safe about whitespace
    pat = re.escape(target2.strip())
    pat = pat.replace(r'\ ', r'\s+')
    if re.search(pat, text):
        text = re.sub(pat, replacement2.replace('\\', '\\\\'), text)
        print('Replaced chunk 4 with regex')
    else:
        print('Chunk 4 target not found')

path.write_text(text, encoding='utf-8')
