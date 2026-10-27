import sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE), os.path.dirname(os.path.dirname(HERE))]

from semabridge.converter.dax_ast_parser import (
    DaxAstParser, DaxLexer, DaxTokenType, DaxToken, DaxSqlRenderer,
    LiteralNode, FunctionCallNode
)

# Test F1.1, F1.2, F1.3, F1.4, F1.5, F1.6, F1.7, F1.8
from cases import CASES

f1_cases = [c for c in CASES if c["id"].startswith("F1.")]
print(f"Total F1 cases: {len(f1_cases)}")
for c in f1_cases:
    print(f"  {c['id']}: {c['title']} | DAX: {repr(c['dax'][:40])}")
