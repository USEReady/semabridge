import re, sys
sys.path.insert(0, 'src')
from semabridge.converter.semantic_view_to_osi import SemanticViewToOSIConverter, _extract_clause, _split_clause_entries

with open('Scratch/comp_marketing_ddl.txt') as f:
    ddl = f.read()

content = _extract_clause(ddl, "METRICS") or _extract_clause(ddl, "MEASURES")
entries = _split_clause_entries(content)
print('Split into', len(entries))
for e in entries:
    m = re.match(r'(\w+)\."?(\w+)"?\s+AS\s+(.+)', e.strip(), re.IGNORECASE)
    if not m:
        print('FAILED:', e.strip())
