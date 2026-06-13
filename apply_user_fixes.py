import pathlib
import re

path = pathlib.Path('src/semabridge/connectors/snowflake_emitter.py')
text = path.read_text(encoding='utf-8')

# Fix 1: Remove duplicate code block
duplicate_block = """        if is_date_table and model:
            date_dataset = self._get_dataset_by_name(model, table_name)
            all_physical_cols = self._live_schema_metadata.get(base_query_table.upper(), set())
            if not all_physical_cols:
                try:
                    cursor.execute(f"SELECT COLUMN_NAME FROM {self.config.database}.INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = '{self.config.schema_name}' AND TABLE_NAME = '{base_query_table.upper()}'")
                    all_physical_cols = {row[0] for row in cursor.fetchall()}
                except Exception as e:
                    logger.warning(f"Failed to get table columns for {base_query_table}: {e}")
                    all_physical_cols = set()"""

# Find all occurrences of duplicate_block
occurrences = [m.start() for m in re.finditer(re.escape(duplicate_block), text)]
if len(occurrences) > 1:
    # Remove the second occurrence
    start_idx = occurrences[1]
    end_idx = start_idx + len(duplicate_block)
    # also remove any trailing newlines or whitespace
    while end_idx < len(text) and text[end_idx] in (' ', '\t', '\n', '\r'):
        end_idx += 1
    text = text[:start_idx] + text[end_idx:]
    print("Fix 1: Removed duplicate block")
else:
    print("Fix 1: Duplicate block not found or already removed")


# Fix 2: Update _resolve_date_column to strip quotes
resolve_target = """    def _resolve_date_column(self, cursor, model) -> str:
        \"\"\"Find actual date column name from DATE table.\"\"\"
        try:
            cursor.execute(\"\"\"
                SELECT COLUMN_NAME 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_NAME = 'DATE' 
                AND DATA_TYPE IN ('DATE', 'TIMESTAMP_NTZ')
            \"\"\")
            rows = cursor.fetchall()
            if rows:
                return rows[0][0]  # e.g., "COL_DATE"
        except:
            pass
        return "COL_DATE\""""

resolve_replacement = """    def _resolve_date_column(self, cursor, model) -> str:
        \"\"\"Return actual date column name without quotes.\"\"\"
        try:
            cursor.execute(\"\"\"
                SELECT COLUMN_NAME 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_NAME = 'DATE' 
                AND DATA_TYPE IN ('DATE', 'TIMESTAMP_NTZ')
            \"\"\")
            rows = cursor.fetchall()
            if rows:
                return rows[0][0].strip('"').strip("'")
        except:
            pass
        return "COL_DATE\""""

if resolve_target in text:
    text = text.replace(resolve_target, resolve_replacement)
    print("Fix 2: Updated _resolve_date_column")
else:
    print("Fix 2: _resolve_date_column target not found")

# Fix 3: Fix join alias generation in _build_precomputed_column_select
build_target = """    def _build_precomputed_column_select(self, model: Any, target_dataset: str, source_dataset: str, source_column: str, precomputed_column: str) -> tuple[Optional[str], Optional[str]]:
        path = self._find_relationship_path(model, target_dataset, source_dataset)
        if not path:
            logger.warning("No active relationship path from %s to %s; cannot precompute %s.%s", target_dataset, source_dataset, source_dataset, source_column)
            return None, None

        joins: list[str] = []
        current_alias = "f"
        last_alias = "f"
        for idx, edge in enumerate(path):
            next_alias = f"j{idx + 1}"
            from_dataset = edge.get("from_dataset") or edge.get("current_dataset")
            to_dataset = edge.get("to_dataset") or edge.get("next_dataset")
            from_columns = edge.get("from_columns") or edge.get("current_columns") or []
            to_columns = edge.get("to_columns") or edge.get("next_columns") or []
            if not from_dataset or not to_dataset or not from_columns or not to_columns:
                logger.warning("Invalid relationship edge at index %s for %s -> %s: %s", idx, target_dataset, source_dataset, edge)
                return None, None
            from_col = self._resolve_model_column_name(model, from_dataset, from_columns[0])
            to_col = self._resolve_model_column_name(model, to_dataset, to_columns[0])
            joins.append(
                f'LEFT JOIN {self._dataset_source_ref(model, to_dataset)} AS {next_alias} '
                f'ON {current_alias}."{from_col}" = {next_alias}."{to_col}"'
            )
            current_alias = next_alias
            last_alias = next_alias

        source_col = self._resolve_model_column_name(model, source_dataset, source_column)
        select_alias = f'{last_alias}."{source_col}" AS "{precomputed_column}"'
        return "\\n".join(joins), select_alias"""

build_replacement = """    def _build_precomputed_column_select(self, model, target_dataset, source_dataset, source_column, precomputed_column):
        path = self._find_relationship_path(model, target_dataset, source_dataset)
        if not path:
            return None, None

        joins = []
        last_alias = "f"
        
        for idx, edge in enumerate(path, start=1):
            next_alias = f"j{idx}"
            # Extract join columns from edge
            from_dataset = edge.get("from_dataset") or edge.get("current_dataset")
            to_dataset = edge.get("to_dataset") or edge.get("next_dataset")
            from_columns = edge.get("from_columns") or edge.get("current_columns") or []
            to_columns = edge.get("to_columns") or edge.get("next_columns") or []
            
            if not from_dataset or not to_dataset or not from_columns or not to_columns:
                continue
                
            from_col = self._resolve_model_column_name(model, from_dataset, from_columns[0])
            to_col = self._resolve_model_column_name(model, to_dataset, to_columns[0])
            
            join_sql = f'LEFT JOIN {self._dataset_source_ref(model, to_dataset)} AS {next_alias} ON {last_alias}."{from_col}" = {next_alias}."{to_col}"'
            joins.append(join_sql)
            last_alias = next_alias

        source_col = self._resolve_model_column_name(model, source_dataset, source_column)
        select_alias = f'{last_alias}."{source_col}" AS "{precomputed_column}"'
        return "\\n".join(joins), select_alias"""

if build_target in text:
    text = text.replace(build_target, build_replacement)
    print("Fix 3: Updated _build_precomputed_column_select")
else:
    print("Fix 3: _build_precomputed_column_select target not found")

# Fix 4: Ensure precompute_details loop uses consistent alias logic
precomp_target = """                for detail in precompute_details:
                    source_dataset = detail.get("source_dataset", "")
                    source_column = detail.get("source_column", "")
                    precomputed_column = detail.get("precomputed_column", "")
                    path = self._find_relationship_path(model, dataset_name, source_dataset)
                    if not path:
                        logger.warning("No active relationship path from %s to %s; cannot enrich %s", dataset_name, source_dataset, precomputed_column)
                        continue

                    for edge in path:
                        from_dataset = edge.get("from_dataset") or edge.get("current_dataset")
                        to_dataset = edge.get("to_dataset") or edge.get("next_dataset")
                        from_columns = edge.get("from_columns") or edge.get("current_columns") or []
                        to_columns = edge.get("to_columns") or edge.get("next_columns") or []
                        if not from_dataset or not to_dataset or not from_columns or not to_columns:
                            logger.warning("Invalid join edge for %s: %s", precomputed_column, edge)
                            break
                        current_key = str(from_dataset).casefold()
                        next_key = str(to_dataset).casefold()
                        if current_key not in joined_aliases:
                            logger.warning("Join path for %s lost alias at %s", precomputed_column, from_dataset)
                            break
                        edge_key = (
                            current_key,
                            next_key,
                            str(from_columns[0]).casefold(),
                            str(to_columns[0]).casefold(),
                        )
                        if next_key not in joined_aliases:
                            next_alias = f"j{alias_idx}"
                            alias_idx += 1
                            joined_aliases[next_key] = next_alias
                        if edge_key not in joined_edges:
                            from_alias = joined_aliases[current_key]
                            next_alias = joined_aliases[next_key]
                            from_col = self._resolve_model_column_name(model, from_dataset, from_columns[0])
                            to_col = self._resolve_model_column_name(model, to_dataset, to_columns[0])
                            join_clauses.append(
                                f'LEFT JOIN {self._dataset_source_ref(model, to_dataset)} AS {next_alias} '
                                f'ON {from_alias}."{from_col}" = {next_alias}."{to_col}"'
                            )
                            joined_edges.add(edge_key)

                    source_alias = joined_aliases.get(str(source_dataset).casefold())
                    if not source_alias:
                        continue
                    source_col = self._resolve_model_column_name(model, source_dataset, source_column)
                    select_items.append(f'{source_alias}."{source_col}" AS "{precomputed_column}"')"""

precomp_replacement = """                seen_joins = set()
                for detail in precompute_details:
                    source_dataset = detail.get("source_dataset", "")
                    source_column = detail.get("source_column", "")
                    precomputed_column = detail.get("precomputed_column", "")
                    
                    joins_str, sel_alias = self._build_precomputed_column_select(
                        model, dataset_name, source_dataset, source_column, precomputed_column
                    )
                    
                    if joins_str and sel_alias:
                        for join_stmt in joins_str.split('\\n'):
                            if join_stmt and join_stmt not in seen_joins:
                                seen_joins.add(join_stmt)
                                join_clauses.append(join_stmt)
                        select_items.append(sel_alias)"""

if precomp_target in text:
    text = text.replace(precomp_target, precomp_replacement)
    print("Fix 4: Updated precompute_details loop")
else:
    print("Fix 4: precompute_details loop target not found")
    
# Clean up unused alias variables if possible
if "alias_idx = 1" in text:
    text = text.replace("                alias_idx = 1\n", "")
if "joined_aliases =" in text:
    text = re.sub(r'                joined_aliases = [^\n]+\n', '', text)
if "joined_edges:" in text:
    text = re.sub(r'                joined_edges:[^\n]+\n', '', text)


# Fix 5: Log generated SQL for debugging
log_target = """        create_sql = f'CREATE OR REPLACE VIEW {schema_ref}."{enriched_name}" AS\\n{select_clause}\\n{from_clause}'"""
log_replacement = """        create_sql = f'CREATE OR REPLACE VIEW {schema_ref}."{enriched_name}" AS\\n{select_clause}\\n{from_clause}'
        logger.info(f"Generated enriched view SQL for {enriched_name}:\\n{create_sql}")"""

if log_target in text:
    text = text.replace(log_target, log_replacement)
    print("Fix 5: Added debug logging")
else:
    print("Fix 5: Logging target not found")


path.write_text(text, encoding='utf-8')
