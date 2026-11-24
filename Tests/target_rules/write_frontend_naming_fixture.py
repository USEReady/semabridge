"""Writes the browser/backend naming parity fixture (frontend/src/utils/__fixtures__/naming_parity.json).

The browser (collisionResolution.js sanitizeMappingName) keeps its own copy of
the naming rule; frontend/src/utils/namingParity.test.js checks it against
these names, produced by the backend's rule (project_mapping_engine
_default_target_identifier -> target_rules/<platform>/naming.py).

    uv run python Tests/target_rules/write_frontend_naming_fixture.py
"""
import json
from pathlib import Path

from semabridge.api.services.project_mapping_engine import _default_target_identifier

HERE = Path(__file__).resolve().parent
NAMES = json.loads((HERE / "fixtures" / "naming_baseline.json").read_text(encoding="utf-8"))["names"]
OUT = HERE.parents[1] / "frontend" / "src" / "utils" / "__fixtures__" / "naming_parity.json"

data = {
    t: {k: {n: _default_target_identifier(n, t, k) for n in NAMES if n.strip()} for k in ("column", "metric")}
    for t in ("snowflake", "databricks")
}
OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
print("wrote", OUT)
