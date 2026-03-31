import re

file_path = "src/semabridge/connectors/snowflake_emitter.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# Pattern for simple connect (Chunks 1, 5, 6)
# Match up to role=self.config.role,
pattern_simple = re.compile(
    r'(import\s+snowflake\.connector\n\s*?)?(?P<assign_var>[\w\.]+)\s*=\s*snowflake\.connector\.connect\(\n\s*user=self\.config\.user,\n\s*password=self\.config\.password\.get_secret_value\(\),\n\s*account=self\.config\.account,\n\s*warehouse=self\.config\.warehouse,\n\s*database=self\.config\.database,\n\s*schema=self\.config\.schema_name,\n\s*role=self\.config\.role,\n\s*\)',
    re.MULTILINE
)

def replace_simple(m):
    import_stmt = m.group(1) or ""
    var = m.group('assign_var')
    # Use proper indent
    indent = " " * (m.start() - content.rfind("\n", 0, m.start()) - 1)
    if not indent:
        indent = "        "  # fallback
    
    # We don't want to duplicate import statement if it exists
    res = ""
    if import_stmt:
        res += import_stmt 
    res += f"from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs\n"
    res += f"{indent}kw = get_snowflake_connect_kwargs(self.config)\n"
    res += f"{indent}{var} = snowflake.connector.connect(**kw)"
    return res

content = pattern_simple.sub(replace_simple, content)

# Pattern for session connect (Chunk 2)
pattern_session = re.compile(
    r'(?P<assign_var>self\._session_conn)\s*=\s*snowflake\.connector\.connect\(\n\s*user=self\.config\.user,\n\s*password=self\.config\.password\.get_secret_value\(\),\n\s*account=self\.config\.account,\n\s*warehouse=self\._resolve_warehouse\(operation\),\n\s*database=self\.config\.database,\n\s*schema=self\.config\.schema_name,\n\s*role=self\.config\.role,\n\s*session_parameters=\{\n\s*"QUERY_TAG":\s*self\.sf_behavior\.query_tag\s*or\s*"Semabridge_Connector"\n\s*\},\n\s*\)',
    re.MULTILINE
)

def replace_session(m):
    var = m.group('assign_var')
    indent = " " * (m.start() - content.rfind("\n", 0, m.start()) - 1)
    if not indent:
        indent = "        "
    res = f"from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs\n"
    res += f"{indent}kw = get_snowflake_connect_kwargs(self.config)\n"
    res += f"{indent}kw[\"warehouse\"] = self._resolve_warehouse(operation)\n"
    res += f"{indent}if \"session_parameters\" not in kw:\n{indent}    kw[\"session_parameters\"] = {{}}\n"
    res += f"{indent}kw[\"session_parameters\"][\"QUERY_TAG\"] = self.sf_behavior.query_tag or \"Semabridge_Connector\"\n"
    res += f"{indent}{var} = snowflake.connector.connect(**kw)"
    return res

content = pattern_session.sub(replace_session, content)

# Pattern for regular parameterized connect (Chunks 3, 4, 7)
pattern_param = re.compile(
    r'(import\s+snowflake\.connector\n\s*?)?(?P<assign_var>[\w\.]+)\s*=\s*snowflake\.connector\.connect\(\n\s*user=self\.config\.user,\n\s*password=self\.config\.password\.get_secret_value\(\),\n\s*account=self\.config\.account,\n\s*warehouse=self\.config\.warehouse,\n\s*database=self\.config\.database,\n\s*schema=self\.config\.schema_name,\n\s*role=self\.config\.role,\n\s*session_parameters=\{\n\s*"QUERY_TAG":\s*(?P<tag>[^}]+)\n\s*\}(?:,)?\n\s*\)',
    re.MULTILINE
)

def replace_param(m):
    import_stmt = m.group(1) or ""
    var = m.group('assign_var')
    tag = m.group('tag').strip()
    indent = " " * (m.start() - content.rfind("\n", 0, m.start()) - 1)
    if not indent:
        indent = "        "
    
    res = ""
    if import_stmt:
        res += import_stmt
    res += f"from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs\n"
    res += f"{indent}kw = get_snowflake_connect_kwargs(self.config)\n"
    res += f"{indent}if \"session_parameters\" not in kw:\n{indent}    kw[\"session_parameters\"] = {{}}\n"
    res += f"{indent}kw[\"session_parameters\"][\"QUERY_TAG\"] = {tag}\n"
    res += f"{indent}{var} = snowflake.connector.connect(**kw)"
    return res

content = pattern_param.sub(replace_param, content)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(content)

print(f"Replaced password calls. Remaining occurrences: {content.count('.get_secret_value()')}")
