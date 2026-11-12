"""O01 - Why does 'Generate from OSI' return 404? (offline, no server needed)

Run from the repo root inside the project venv:
    uv run python ontology_tests/o01_loader_root_cause.py

Checks the three things _load_osi_model() (export_router.py) relies on, and
whether its broad 'except' turns an internal error into 'not found' (404).
"""
import inspect
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import Report  # noqa: E402

rep = Report("O01 OSI loader root cause")
repo = Path.cwd()
sys.path.insert(0, str(repo / "src"))

src_file = repo / "src/semabridge/api/routers/export_router.py"
if not src_file.exists():
    rep.fail("run from repo root", f"{src_file} not found")
    sys.exit(rep.done())

src = src_file.read_text(encoding="utf-8")
m = re.search(r"async def _load_osi_model\(.*?(?=\n(?:async )?def |\Z)", src, re.S)
body = m.group(0) if m else ""
rep.check(bool(body), "_load_osi_model found in export_router.py")

# 1. get_db_session
import importlib
deps = importlib.import_module("semabridge.api.dependencies")
uses_db_session = "get_db_session" in body
has_db_session = hasattr(deps, "get_db_session")
if uses_db_session:
    rep.check(has_db_session, "semabridge.api.dependencies.get_db_session exists",
              "missing -> ImportError on every call (this is the error in your log)")

# 2. ModelRepository import path
imp = re.search(r"from (semabridge\.repository\.[\w.]+) import ModelRepository", body)
if imp:
    mod = importlib.import_module(imp.group(1))
    rep.check(hasattr(mod, "ModelRepository"), f"{imp.group(1)}.ModelRepository exists",
              "should be semabridge.repository.model_repository.ModelRepository")

# 3. get_snapshot signature
try:
    from semabridge.repository.model_repository import ModelRepository
    params = list(inspect.signature(ModelRepository.get_snapshot).parameters)
    called_with_project = re.search(r"get_snapshot\(\s*project_id", body) is not None
    if called_with_project:
        rep.check("project_id" in params, "get_snapshot(project_id, ...) matches the repository",
                  f"real signature is get_snapshot{tuple(params)}; latest snapshot = get_head(project_id)")
    rep.check(hasattr(ModelRepository, "get_head"), "ModelRepository.get_head(project_id) available")
except Exception as e:
    rep.fail("import ModelRepository", str(e))

# 4. snapshot payload shape
uses_sml_blob = "sml_blob" in body
rep.check(uses_sml_blob, "loader reads snapshot.sml_blob",
          "Snapshot is a dataclass with .sml_blob (SML); loader uses snapshot.get('osi_model') which does not exist")

# 5. error masking
masks = re.search(r"except Exception[^\n]*:\s*\n\s*logger\.warning\([^\n]*\)\s*\n\s*return None", body) is not None
rep.check(not masks, "internal errors are not converted to None/404",
          "broad 'except Exception: return None' -> caller raises 404 'No OSI model found'")

sys.exit(rep.done())
