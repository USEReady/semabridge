"""Every synced project has an ontology.

The ontology (business definitions of the project's tables and measures) is
what the BI-Truth Suite, Ask AI, Blast Radius and the Metric Comparator read.
It used to exist only after someone clicked "Generate from OSI"; a project
synced without that click showed "no project domains" everywhere.

After a successful sync, and once at startup for projects that synced before
this existed, a project without a domain gets one generated from its synced
model -- the same code path as the button (``/api/ontology/from-osi``). A
project that already has a domain is never touched (user edits are kept).
Disable with ``SEMABRIDGE_AUTO_ONTOLOGY=false``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return str(os.environ.get("SEMABRIDGE_AUTO_ONTOLOGY", "true")).strip().lower() not in {"0", "false", "no", "off"}


def has_ontology(project_id: str) -> bool:
    from semabridge.ontology.store import ontology_store

    return bool(ontology_store.domains_for(project_id))


def _run_coro(coro: Any) -> Any:
    """Run ``coro`` to completion from sync code, inside or outside an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: Dict[str, Any] = {}

    def _target() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def ensure_project_ontology(project_id: str) -> Optional[Dict[str, Any]]:
    """Generate the project's ontology from its synced model if it has none.

    Returns the generation result, or None when nothing was done (disabled,
    already present, or no synced model yet). Never raises.
    """
    pid = str(project_id or "").strip()
    if not pid or not enabled():
        return None
    try:
        if has_ontology(pid):
            return None
        from semabridge.api.routers.domain_ontology_router import FromOsiRequest, bootstrap_from_osi

        # No request: a server-side step, not a user's edit (ownership checks
        # apply to users; a new domain has no owner to check against).
        result = _run_coro(bootstrap_from_osi(FromOsiRequest(project_id=pid), None))
        logger.info("Ontology generated for project %s from its synced model", pid)
        return result if isinstance(result, dict) else {"success": True}
    except Exception as exc:  # noqa: BLE001 -- never fails a sync
        detail = getattr(exc, "detail", None) or exc
        logger.info("Ontology not generated for project %s: %s", pid, detail)
        return None


def _synced_project_ids() -> List[str]:
    from semabridge.api.services.project_shared import _compat_projects
    from semabridge.api.services.run_records import list_project_runs

    out = []
    for pid in list(_compat_projects.keys()):
        runs = list_project_runs(pid, limit=20) or []
        if any(str(r.get("status") or "").lower() in ("success", "warning") for r in runs):
            out.append(pid)
    return out


def backfill(project_ids: Optional[Iterable[str]] = None) -> int:
    """Generate ontologies for synced projects that have none; returns how many were created."""
    if not enabled():
        return 0
    created = 0
    try:
        ids = list(project_ids) if project_ids is not None else _synced_project_ids()
    except Exception as exc:  # noqa: BLE001
        logger.info("Ontology backfill skipped: %s", exc)
        return 0
    for pid in ids:
        if ensure_project_ontology(pid):
            created += 1
    return created


# ``db.schema."catalog.schema.view"``: an already-qualified Databricks view
# name that was prefixed with the project's default catalog/schema.
_DOUBLE_QUALIFIED = re.compile(r'^[^"]*"([^"]+\.[^"]+)"$')


def fix_view_name(name: Optional[str]) -> Optional[str]:
    """The real view name of a double-qualified one; other names unchanged."""
    m = _DOUBLE_QUALIFIED.match(str(name or "").strip())
    return m.group(1) if m else name


def repair_view_names() -> int:
    """Rewrite domains whose semantic view name was double-qualified. Returns how many."""
    from semabridge.ontology.store import ontology_store

    fixed = 0
    for dom in list(ontology_store.domains.values()):
        names = [dom.semantic_view_name] + [m.semantic_view_name for m in dom.metrics]
        if not any(fix_view_name(n) != n for n in names if n):
            continue
        updated = dom.model_copy(deep=True)
        updated.semantic_view_name = fix_view_name(updated.semantic_view_name)
        for m in updated.metrics:
            m.semantic_view_name = fix_view_name(m.semantic_view_name)
        try:
            ontology_store.add_or_update_domain(updated, save_to_disk=True, original=dom.domain)
            fixed += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not repair the view name of domain %s: %s", dom.domain, exc)
    if fixed:
        logger.info("Repaired the semantic view name of %d domain(s)", fixed)
    return fixed


def start_backfill_thread() -> None:
    """Startup: backfill in the background so the API starts immediately."""
    if not enabled():
        return

    def _run() -> None:
        try:
            repair_view_names()
        except Exception as exc:  # noqa: BLE001
            logger.info("View name repair skipped: %s", exc)
        n = backfill()
        if n:
            logger.info("Ontology backfill: generated %d project ontolog%s", n, "y" if n == 1 else "ies")

    threading.Thread(target=_run, name="ontology-backfill", daemon=True).start()


def _norm_name(value: Any) -> str:
    return re.sub(r"[^0-9a-z]+", "", str(value or "").lower())


def sync_planned_view_names(project_id: str, plan: Optional[Dict[str, Any]]) -> int:
    """After a Databricks planner deploy: point the project's ontology at the
    views the plan created. The planner may make several metric views per
    model (one per fact table) with naming-v2 names, so each metric gets its own
    view; the domain keeps the view holding most of its metrics. Returns the
    number of metrics updated. Never raises."""
    pid = str(project_id or "").strip()
    views = list((plan or {}).get("views") or [])
    if not pid or not views:
        return 0
    try:
        from semabridge.ontology.store import ontology_store

        settings = (plan or {}).get("settings") or {}
        prefix = ".".join(p for p in (settings.get("output_catalog"), settings.get("output_schema")) if p)

        def fqn(view_name: str) -> str:
            return f"{prefix}.{view_name}" if prefix else view_name

        view_of: Dict[str, str] = {}
        for v in views:
            for m in v.get("measures") or []:
                for key in (m.get("source_name"), m.get("display_name"), m.get("name")):
                    if key:
                        view_of.setdefault(_norm_name(key), fqn(str(v.get("name"))))
        changed = 0
        for dom in list(ontology_store.domains_for(pid)):
            updated = dom.model_copy(deep=True)
            counts: Dict[str, int] = {}
            touched = False
            for m in updated.metrics:
                target = next((view_of[k] for k in (_norm_name(m.name), _norm_name(getattr(m, "label", None)),
                                                    _norm_name(getattr(m, "logical_name", None))) if k in view_of), None)
                if not target:
                    continue
                counts[target] = counts.get(target, 0) + 1
                if m.semantic_view_name != target:
                    m.semantic_view_name = target
                    touched = True
                    changed += 1
            if counts:
                main = max(counts, key=counts.get)
                if updated.semantic_view_name != main:
                    updated.semantic_view_name = main
                    touched = True
            if touched:
                ontology_store.add_or_update_domain(updated, save_to_disk=True, original=dom.domain)
        if changed:
            logger.info("Ontology of %s now points at %d planned Databricks view(s)", pid, len(set(view_of.values())))
        return changed
    except Exception as exc:  # noqa: BLE001
        logger.info("Ontology view names for %s not updated: %s", pid, exc)
        return 0
