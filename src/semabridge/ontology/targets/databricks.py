"""Databricks facts for the ontology screens, read from existing artifacts only.

* CURRENT PLAN -- ``databricks_view_plan.json`` of this project/model: every
  occurrence (view + name) with its final expression, plan revision,
  approximation and readiness; ``left_out[]`` with the reason. Dry runs run as a
  preview project (``preview-<hash>``); such a plan is used only when it records
  THIS project as its owner (``meta.cache_ref.binding_project_id``) and the same
  model -- never another project's or model's artifacts.
* LAST VERIFIED DEPLOYMENT -- the deploy records' newest VERIFIED state per view
  (an applied_unverified or failed write never replaces it), and the exact
  definition from that run's revision bundle (``views/<revision>/<view>.yaml``),
  checked against the recorded body hash. If that bundle is gone or differs, the
  verified expression is reported UNAVAILABLE -- never replaced by the plan.

Planned and verified are kept apart: a newer dry run never overwrites or
relabels what was last verified. Nothing here writes anything.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from semabridge.ontology.targets.base import (
    ItemFacts,
    Occurrence,
    OccurrenceState,
    OntologyTargetAdapter,
    TargetFacts,
    item_key,
)

logger = logging.getLogger(__name__)

PLAN_JSON = "databricks_view_plan.json"
VIEWS_DIR = "views"
MANIFEST = "manifest.json"


def _norm_fqn(name: str) -> str:
    from semabridge.target_backends.databricks.guard import normalize_name

    return normalize_name(name)


def _is_approximate(entry: Dict[str, Any]) -> bool:
    texts = list(entry.get("warnings") or []) + [str(entry.get("comment") or "")]
    return any(str(t).strip().lower().startswith("approximate") or "approximate:" in str(t).lower() for t in texts)


class DatabricksOntologyAdapter(OntologyTargetAdapter):
    target = "databricks"

    def __init__(self, output_root: Path = Path("output")) -> None:
        self.output_root = Path(output_root)

    # --- locating this project's artifacts ---------------------------------------------------

    def _candidate_dirs(self, project_id: str, model_key: str) -> List[Tuple[Path, Dict[str, Any]]]:
        """(out_dir, plan) for every plan folder that belongs to project_id/model_key."""
        from semabridge.target_backends.databricks.replan import _safe, output_dir

        base = self.output_root / "databricks"
        own = output_dir(project_id, model_key or None, root=self.output_root)
        found: List[Tuple[Path, Dict[str, Any]]] = []
        if not base.is_dir():
            return found
        for top in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith("_")):
            d = top / _safe(model_key) if model_key else top
            plan = self._read_json(d / PLAN_JSON)
            if plan is None:
                continue
            if d.resolve() == own.resolve():
                found.append((d, plan))
                continue
            ref = (plan.get("meta") or {}).get("cache_ref") or {}
            if str(ref.get("binding_project_id") or "") != str(project_id):
                continue
            if str(ref.get("model_subdir") or "") != str(model_key or ""):
                continue
            found.append((d, plan))
        return found

    @staticmethod
    def _read_json(path: Path) -> Optional[Dict[str, Any]]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    # --- the read model -------------------------------------------------------------------------

    def facts(self, project_id: str, model_key: str = "") -> TargetFacts:
        facts = TargetFacts(target=self.target, available=False)
        candidates = self._candidate_dirs(project_id, model_key)
        current = max(candidates, key=lambda c: int((c[1].get("meta") or {}).get("revision") or 0), default=None)
        destination = None
        if current is not None:
            out_dir, plan = current
            meta = plan.get("meta") or {}
            facts.available = True
            facts.plan_revision = int(meta["revision"]) if meta.get("revision") is not None else None
            facts.plan_status = str(meta.get("status") or "") or None
            destination = str((plan.get("settings") or {}).get("output_location") or "") or None
            facts.destination = destination
            self._add_planned(facts, plan, destination)
        else:
            facts.notes.append("No Databricks plan for this project yet: run the dry run (step 5).")

        try:
            from semabridge.target_backends.databricks.deploy import store

            states = store.deploy_states(project_id, model_key)
        except Exception as exc:
            logger.warning("Databricks deploy records unreadable for ontology facts: %s", type(exc).__name__)
            facts.notes.append("Deployment records could not be read; deployed state is unavailable.")
            states = {}
        if states:
            facts.available = True
            self._add_verified(facts, states, [d for d, _p in candidates])
        return facts

    def _occurrence(self, facts: TargetFacts, kind: str, table: str, name: str) -> ItemFacts:
        key = item_key(kind, table, name)
        item = facts.items.get(key)
        if item is None:
            item = facts.items[key] = ItemFacts(kind=kind, table=table, name=name)
        return item

    def _add_planned(self, facts: TargetFacts, plan: Dict[str, Any], destination: Optional[str]) -> None:
        revision = facts.plan_revision
        for view in plan.get("views") or []:
            vname = str(view.get("name") or "")
            fqn = f"{destination}.{vname}" if destination else vname
            for f in view.get("fields") or []:
                sid = f.get("source_id") or {}
                item = self._occurrence(facts, "field", str(sid.get("table") or f.get("dataset") or ""),
                                        str(sid.get("name") or f.get("column") or ""))
                item.occurrences.append(Occurrence(
                    occurrence_id=f"{vname}/{f.get('name')}", view=fqn, name=str(f.get("name") or ""),
                    planned=OccurrenceState(revision=revision, expression=f.get("expr"),
                                            readiness=f.get("readiness"), status="planned")))
            for m in view.get("measures") or []:
                sid = m.get("source_id") or {}
                item = self._occurrence(facts, "measure", "", str(m.get("source_name") or sid.get("name") or ""))
                item.occurrences.append(Occurrence(
                    occurrence_id=f"{vname}/{m.get('name')}", view=fqn, name=str(m.get("name") or ""),
                    planned=OccurrenceState(revision=revision, expression=m.get("expr"),
                                            approximate=_is_approximate(m), readiness=m.get("readiness"),
                                            status="planned")))
        for lo in plan.get("left_out") or []:
            item = self._occurrence(facts, "measure", "", str(lo.get("measure") or ""))
            item.left_out = {k: lo.get(k) for k in ("reason", "detail", "root_cause")}

    def _add_verified(self, facts: TargetFacts, states: Dict[str, Dict[str, Any]], dirs: Iterable[Path]) -> None:
        dirs = list(dirs)
        by_occ: Dict[Tuple[str, str], Occurrence] = {}
        for item in facts.items.values():
            for occ in item.occurrences:
                by_occ[(_norm_fqn(occ.view), occ.name)] = occ
        for fqn, entry in states.items():
            verified, latest = entry.get("verified"), entry.get("latest") or {}
            unresolved = bool(latest) and not latest.get("verified") and latest is not verified
            view = fqn.rsplit(".", 1)[-1]
            doc, why = (None, None)
            if verified:
                doc, why = self._deployed_definition(dirs, view, verified)
            entries = self._entries(doc) if doc else {}
            # Every name present in the verified definition, or planned for this view.
            names = set(entries) | {n for (v, n) in by_occ if v == fqn}
            for name in names:
                occ = by_occ.get((fqn, name))
                if verified:
                    e = entries.get(name)
                    state = OccurrenceState(
                        revision=verified.get("plan_revision"), run_id=verified.get("run_id"),
                        expression=(e or {}).get("expr") if e else None,
                        approximate=_is_approximate(e or {}),
                        status="unavailable" if why else "verified" if e else "not_in_deployed_view",
                        note=why or ("live state unresolved: a later write is not verified" if unresolved else None))
                else:
                    state = OccurrenceState(run_id=latest.get("run_id"), revision=latest.get("plan_revision"),
                                            status=str(latest.get("outcome") or "unavailable"),
                                            note="never verified live")
                if occ is not None:
                    new = Occurrence(occ.occurrence_id, occ.view, occ.name, occ.planned, state)
                    self._replace(facts, occ, new)
                    by_occ[(fqn, name)] = new
                elif e := entries.get(name):
                    # Deployed but no longer planned: still show what is live.
                    kind, table, src = self._source_of(e)
                    item = self._occurrence(facts, kind, table, src or name)
                    item.occurrences.append(Occurrence(f"{view}/{name}", fqn, name, None, state))

    @staticmethod
    def _replace(facts: TargetFacts, old: Occurrence, new: Occurrence) -> None:
        for item in facts.items.values():
            for i, occ in enumerate(item.occurrences):
                if occ is old:
                    item.occurrences[i] = new
                    return

    @staticmethod
    def _entries(doc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for key in ("fields", "dimensions", "measures"):
            for e in doc.get(key) or []:
                if isinstance(e, dict) and e.get("name"):
                    out[str(e["name"])] = {**e, "_kind": "measure" if key == "measures" else "field"}
        return out

    @staticmethod
    def _source_of(entry: Dict[str, Any]) -> Tuple[str, str, str]:
        # A deployed YAML entry carries no source id; its display name is the best label.
        return str(entry.get("_kind") or "field"), "", str(entry.get("display_name") or "")

    def _deployed_definition(self, dirs: List[Path], view: str, verified: Dict[str, Any]
                             ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """(parsed YAML of the deployed definition, None) or (None, why unavailable)."""
        revision = verified.get("plan_revision")
        if revision is None:
            return None, "deployed revision not recorded"
        for d in dirs:
            rev_dir = d / VIEWS_DIR / str(int(revision))
            path = rev_dir / f"{view}.yaml"
            if not path.is_file():
                continue
            raw = path.read_bytes()
            want = str(verified.get("body_sha256") or "")
            manifest = self._read_json(rev_dir / MANIFEST) or {}
            listed = next((v.get("body_sha256") for v in manifest.get("views") or [] if v.get("view") == view), None)
            if want and listed and str(listed) != want:
                return None, "the kept bundle differs from what was deployed"
            if listed and hashlib.sha256(raw).hexdigest() != str(listed):
                return None, "the kept bundle file was changed after it was written"
            recorded = str(verified.get("definition_hash") or "")
            if recorded:
                from semabridge.target_backends.databricks.deploy.definitions import definition_hash
                try:
                    if definition_hash(raw.decode("utf-8")) != recorded:
                        return None, "the kept bundle differs from what was verified live"
                except Exception:
                    return None, "the kept bundle could not be read"
            try:
                doc = yaml.safe_load(raw.decode("utf-8"))
            except Exception:
                return None, "the kept bundle could not be read"
            return (doc if isinstance(doc, dict) else None), (None if isinstance(doc, dict) else "unreadable bundle")
        return None, f"the bundle of revision {revision} is no longer kept"
