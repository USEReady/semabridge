"""Revision-consistent bundles of rendered metric-view definitions (Phase 3.7).

    output/databricks/<project>[/<model>]/views/
        <plan revision>/<view>.yaml, manifest.json     -- one folder per plan revision
        current.json                                    -- which revision is current

* Atomic: every file is written into a temporary folder first; only when all
  views rendered and the manifest is complete is the folder renamed into
  place. A failed or partial bundle never becomes a revision folder, and
  ``current.json`` (replaced atomically) only ever moves forward.
* Deployability: 3.8 accepts only the current bundle of the current valid plan
  revision, and never a probe bundle (probe bundles live under ``probes/``
  and never become current). After a re-plan, older bundles are obsolete.
* The local body hash says what was written; drift against what is actually
  deployed is a separate 3.8 check that reads the live definition back.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from semabridge.target_backends.databricks.writer import WRITER_REVISION, RenderedView

VIEWS_DIR = "views"
PROBES_DIR = "probes"
CURRENT = "current.json"
MANIFEST = "manifest.json"
KEEP = 5                      # revision folders kept (older ones are pruned)

_lock = threading.Lock()


class BundleError(RuntimeError):
    pass


def _views_dir(out_dir: Path, probe: bool) -> Path:
    return out_dir / (PROBES_DIR if probe else VIEWS_DIR)


def build_manifest(plan: Dict[str, Any], rendered: List[RenderedView], *, plan_revision: int,
                   catalog: str, schema: str, probe: bool, run_id: Optional[str]) -> Dict[str, Any]:
    tables = {t["dataset"]: t for t in plan.get("tables") or []}
    readiness = {v["name"]: v.get("readiness") for v in plan.get("views") or []}
    return {
        "plan_revision": int(plan_revision),
        "writer_revision": WRITER_REVISION,
        "probe": bool(probe),
        "destination": {"catalog": catalog, "schema": schema},
        "run_id": run_id,
        "written_at": datetime.now(timezone.utc).isoformat(),
        "views": [{
            "view": r.view, "fqn": r.fqn, "file": f"{r.view}.yaml", "body_sha256": r.body_sha256,
            "readiness": readiness.get(r.view), "tables": list(r.tables), "warnings": list(r.warnings),
        } for r in rendered],
        "tables": [dict(t) for t in tables.values()],
        "proposed_tables_not_created": sorted(ds for ds, t in tables.items() if t.get("status") != "bound"),
        "blockers": [dict(b) for b in plan.get("blockers") or []],
    }


def write_bundle(out_dir: Path, plan: Dict[str, Any], rendered: List[RenderedView], *, plan_revision: int,
                 catalog: str, schema: str, probe: bool = False, run_id: Optional[str] = None) -> Dict[str, Any]:
    """Write the bundle for ``plan_revision`` atomically; returns its manifest.
    Does NOT make it current (see ``set_current``)."""
    base = _views_dir(out_dir, probe)
    base.mkdir(parents=True, exist_ok=True)
    final = base / str(int(plan_revision))
    tmp = base / f".tmp-{int(plan_revision)}-{uuid.uuid4().hex}"
    manifest = build_manifest(plan, rendered, plan_revision=plan_revision, catalog=catalog, schema=schema,
                              probe=probe, run_id=run_id)
    try:
        tmp.mkdir()
        # Exact bytes (no newline translation): the manifest hashes must match the files.
        for r in rendered:
            (tmp / f"{r.view}.yaml").write_bytes(r.yaml.encode("utf-8"))
        (tmp / MANIFEST).write_bytes(json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"))
        with _lock:
            if final.exists():
                raise BundleError(f"a bundle for plan revision {plan_revision} already exists")
            os.replace(tmp, final)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return manifest


def read_current(out_dir: Path) -> Dict[str, Any]:
    try:
        return json.loads((_views_dir(out_dir, False) / CURRENT).read_text(encoding="utf-8"))
    except Exception:
        return {}


def set_current(out_dir: Path, plan_revision: int, *, reset: bool = False) -> bool:
    """Make ``plan_revision`` the current bundle -- only if it exists and is newer
    than the current one (``reset``: a new dry run replaces whatever was current)."""
    base = _views_dir(out_dir, False)
    with _lock:
        if not (base / str(int(plan_revision)) / MANIFEST).exists():
            return False
        cur = read_current(out_dir)
        if not reset and int(cur.get("plan_revision") or -1) >= int(plan_revision):
            return False
        tmp = base / f"{CURRENT}.{uuid.uuid4().hex}.tmp"
        tmp.write_bytes(json.dumps({"plan_revision": int(plan_revision),
                                    "writer_revision": WRITER_REVISION}).encode("utf-8"))
        os.replace(tmp, base / CURRENT)
        _prune(base, keep_revision=int(plan_revision))
        return True


def _prune(base: Path, keep_revision: int) -> None:
    revs = sorted((int(p.name) for p in base.iterdir() if p.is_dir() and p.name.isdigit()), reverse=True)
    for rev in revs[KEEP:]:
        if rev != keep_revision:
            shutil.rmtree(base / str(rev), ignore_errors=True)


def deployable_bundle(out_dir: Path, plan_revision: int) -> Dict[str, Any]:
    """The manifest 3.8 may deploy: the CURRENT bundle, of exactly this plan
    revision, not a probe bundle, with every view file present and unchanged."""
    import hashlib

    cur = read_current(out_dir)
    if int(cur.get("plan_revision") or -1) != int(plan_revision):
        raise BundleError(f"bundle for plan revision {plan_revision} is not current (current: {cur.get('plan_revision')})")
    folder = _views_dir(out_dir, False) / str(int(plan_revision))
    try:
        manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except Exception as exc:
        raise BundleError(f"bundle manifest unreadable: {exc}") from None
    if manifest.get("probe"):
        raise BundleError("a probe bundle is never deployable")
    for v in manifest.get("views") or []:
        path = folder / v["file"]
        if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != v["body_sha256"]:
            raise BundleError(f"view file {v['file']} is missing or changed since it was written")
    return manifest


def render_and_write(out_dir: Path, plan: Dict[str, Any], *, catalog: Optional[str], schema: Optional[str],
                     model_label: str, plan_revision: int, run_id: Optional[str] = None) -> Dict[str, Any]:
    """Render every view and write the bundle. Returns the summary stored in the
    plan's meta: {status: written | unavailable, error, revision, views:[...]}
    plus the rendered definitions for the screen. Never raises for a writer
    problem: the plan then says the definitions are unavailable, and why."""
    from semabridge.target_backends.databricks.writer import WriterError, render_plan

    try:
        rendered = render_plan(plan, catalog=catalog, schema=schema, model_label=model_label)
        write_bundle(out_dir, plan, rendered, plan_revision=plan_revision, catalog=str(catalog), schema=str(schema),
                     run_id=run_id)
    except (WriterError, BundleError) as exc:
        code = getattr(exc, "code", "bundle_error")
        return {"status": "unavailable", "error": str(exc), "error_code": code, "revision": int(plan_revision),
                "views": [], "definitions": []}
    return {
        "status": "written", "error": None, "error_code": None, "revision": int(plan_revision),
        "writer_revision": WRITER_REVISION,
        "views": [{"view": r.view, "body_sha256": r.body_sha256, "warnings": list(r.warnings)} for r in rendered],
        "definitions": [r.as_dict() for r in rendered],
    }


def attach_definitions(plan: Dict[str, Any], out_dir: Path, *, catalog: Optional[str], schema: Optional[str],
                       model_label: str, run_id: Optional[str] = None) -> Dict[str, Any]:
    """Render + write the bundle for ``plan`` (its meta.revision) and put the result
    on the plan: ``plan["definitions"]`` (for the screen) and
    ``plan["meta"]["definitions"]`` (status, revision, hashes). When the
    definitions cannot be written, a model-wide publication blocker says why."""
    revision = int((plan.get("meta") or {}).get("revision") or 0)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = render_and_write(out_dir, plan, catalog=catalog, schema=schema, model_label=model_label,
                               plan_revision=revision, run_id=run_id)
    plan["definitions"] = summary.pop("definitions")
    plan.setdefault("meta", {})["definitions"] = summary
    if summary["status"] != "written":
        plan.setdefault("blockers", []).append({
            "code": "view_definitions_unavailable",
            "message": f"the metric-view definitions could not be written: {summary['error']}",
            "view": None, "source_path": None, "details": {"error_code": summary["error_code"]},
            "scope": "model", "table": None, "views": [],
        })
        plan.setdefault("summary", {})["publication_blocked"] = True
    return summary
