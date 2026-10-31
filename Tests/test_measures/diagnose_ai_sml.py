import sys, os, re
sys.path.insert(0, ".")
import openpyxl

from semabridge.converter.dax_translator import DAXTranslator
# Load SML model for Artificial Intelligence
sml_path = r"output\reverse\proj-artificial-intelligence-sample\sml\model.yaml"
if not os.path.exists(sml_path):
    print("SML file not found:", sml_path)
    sys.exit(1)

import yaml
with open(sml_path, "r", encoding="utf-8") as f:
    raw_sml = yaml.safe_load(f)

# Build schema lookup
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.utils.naming import to_alias

# Let's inspect each metric in raw_sml
datasets = raw_sml.get("datasets", [])
col_lookup = {}
aliases = {}
for ds in datasets:
    name = ds.get("name")
    alias = to_alias(name)
    aliases[name] = alias
    cols = {c.get("name") for c in ds.get("columns", [])}
    col_lookup[name] = cols

translator = DAXTranslator()
metrics = raw_sml.get("metrics", [])
print(f"Total metrics in SML: {len(metrics)}")

class DummyMetric:
    def __init__(self, d):
        self.unique_name = d.get("name")
        self.expression = d.get("expr") or d.get("expression")
        self.dataset = d.get("dataset")
        self.sql_expression = d.get("sql_expression")

dummy_metrics = [DummyMetric(m) for m in metrics]

for idx, m in enumerate(dummy_metrics, 1):
    dax = m.expression
    ds = m.dataset
    name = m.unique_name
    print(f"\n[{idx}] {ds}[{name}]")
    print(f"DAX: {repr(dax[:80] if dax else None)}")
    
    # Try translate
    res = translator.translate(
        dax,
        to_alias(ds),
        ds,
        metric_name=name,
        metrics_context=dummy_metrics,
        dataset_col_lookup=col_lookup,
        dataset_aliases=aliases,
        skip_tier5=True,
    )
    print(f"Result: success={res.is_success}, tier={res.tier}")
    if res.is_success:
        print(f"SQL: {res.sql}")
    else:
        # Let's diagnose WHY it failed
        # Check Tier 1 regex
        # Check Tier 2
        # Check AST parser
        from semabridge.converter.dax_ast_parser import DaxAstParser, DaxSqlRenderer
        try:
            parser = DaxAstParser()
            ast = parser.parse(dax)
            renderer = DaxSqlRenderer(table_alias=to_alias(ds))
            sql = renderer.render(ast)
            print(f"  AST parse direct: {sql}")
        except Exception as e:
            print(f"  AST parse error: {e}")
