import os, sys
sys.path.insert(0, 'src')
from semabridge.converter.semantic_view_to_osi import SemanticViewToOSIConverter

converter = SemanticViewToOSIConverter()
with open('Scratch/comp_marketing_ddl.txt') as f:
    ddl = f.read()

table_map = converter._parse_tables_clause(ddl)          
datasets = converter._build_datasets(table_map, {}, ddl)
metrics = converter._parse_measures_clause(ddl, table_map)

print('converted', len(metrics), 'metrics')
print('converted', len(datasets), 'datasets')
