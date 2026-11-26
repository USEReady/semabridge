"""Deployment intents (Phase 3.8b): a stored preview that a confirmation executes.

* The preview is computed server-side (``prepare``) and stored with a
  FINGERPRINT that binds it to: the target (workspace, warehouse, metastore,
  destination and staging schemas), the deploying identity, the plan revision,
  the bundle hashes, the expected object identities and definitions, the
  capability evidence, the options, and the ordered statements with their stable
  operation ids (sequence numbers).
* The intent fixes the run id and the staging token, so re-preparing under the
  leases reproduces exactly the same statements -- or a different fingerprint.
* Confirmation never takes statements from the browser; it executes the stored
  intent. An intent yields at most one run (``consume_intent``).
* Unused intents expire after 30 minutes and are invalidated by a newer plan.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from typing import Any, Dict, Optional

from semabridge.target_backends.databricks.deploy.prepare import DeployPreview, DeployRequest

INTENT_VERSION = 1


def fingerprint(pv: DeployPreview, req: DeployRequest) -> str:
    def items(xs):
        return [{"kind": i.kind, "name": i.name, "fqn": i.fqn, "action": i.action, "readiness": i.readiness,
                 "reasons": sorted(r["code"] for r in i.reasons),
                 "ids": {k: i.details.get(k) for k in ("uc_object_id", "live_uc_object_id", "planned_uc_object_id",
                                                       "previous_hash", "live_definition_hash", "definition_hash")}}
                for i in xs]

    p = pv.platform or {}
    basis = {
        "version": INTENT_VERSION,
        "plan_revision": pv.plan_revision, "destination": pv.destination, "staging": pv.staging,
        "target": {k: p.get(k) for k in ("workspace_host", "warehouse_id", "metastore", "identity", "dbsql_version")},
        "proof": (p.get("proof") or {}).get("run_id"),
        "options": {"create_stubs": bool(req.create_stubs), "publish_incomplete": bool(req.publish_incomplete),
                    "stub_form": pv.stub_form},
        "bundle": dict(sorted(pv.bundle_hashes.items())),
        "operations": [{"seq": n, "kind": s.kind, "target": s.target, "sha256": s.sha256}
                       for n, s in enumerate(pv.statements, 1)],
        "views": items(pv.views), "stub_tables": items(pv.stub_tables), "sources": items(pv.sources),
        "verified": sorted(pv.verified_objects, key=lambda o: o["fqn"]),
        "model_blockers": sorted(b["code"] for b in pv.model_blockers),
        "destination_access": sorted({a["principal"] for a in pv.destination_access or []}),
        "staging_access": sorted({a["principal"] for a in (p.get("staging_access") or [])}),
    }
    return hashlib.sha256(json.dumps(basis, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def request_fields(req: DeployRequest) -> Dict[str, Any]:
    """What is needed to rebuild the same DeployRequest at confirmation."""
    return {"project_id": req.project_id, "model_key": req.model_key, "out_dir": str(req.out_dir),
            "workspace_host": req.workspace_host, "warehouse_id": req.warehouse_id,
            "allow_list": sorted(req.allow_list or []), "create_stubs": bool(req.create_stubs),
            "publish_incomplete": bool(req.publish_incomplete), "stub_form": req.stub_form,
            "staging": list(req.staging) if req.staging else None,
            "deploy_principals": sorted(req.deploy_principals or [])}


def new_ids() -> Dict[str, str]:
    return {"intent_id": uuid.uuid4().hex, "run_id": uuid.uuid4().hex, "token": secrets.token_hex(4)}


def create_intent(req: DeployRequest, live: Any, *, created_by: Optional[str], store: Any = None,
                  now: Any = None) -> Dict[str, Any]:
    """Preview (read-only) and, when there is something deployable or verifiable,
    store the intent. Returns {"preview", "intent" (or None), "fingerprint"}."""
    from semabridge.target_backends.databricks.deploy.prepare import prepare

    if store is None:
        from semabridge.target_backends.databricks.deploy import store as store_mod

        store = store_mod
    ids = new_ids()
    req.run_id = ids["run_id"]
    pv = prepare(req, live, store=store, now=now, token=ids["token"])
    fp = fingerprint(pv, req)
    out: Dict[str, Any] = {"preview": pv.as_dict(), "intent": None, "fingerprint": fp}
    if pv.status not in ("ready", "no_op"):
        return out
    body = {"request": request_fields(req), "token": ids["token"], "preview": pv.as_dict(),
            "requires_access_acknowledgement": bool(pv.destination_access)}
    saved = store.save_intent(intent_id=ids["intent_id"], run_id=ids["run_id"], project_id=req.project_id,
                              model_key=req.model_key, plan_revision=int(pv.plan_revision or 0), fingerprint=fp,
                              intent=body, created_by=created_by, now=now)
    out["intent"] = {**saved, "fingerprint": fp,
                     "requires_access_acknowledgement": body["requires_access_acknowledgement"]}
    return out


def rebuild_request(intent: Dict[str, Any], *, plan: Dict[str, Any], lease_run_id: Optional[str],
                    overrides: Optional[Dict[str, Any]] = None) -> DeployRequest:
    """The preview's request, with the CURRENT project settings (``overrides``:
    allow_list, staging, deploy_principals, stub_form) where given."""
    from pathlib import Path

    r = {**intent["intent"]["request"], **{k: v for k, v in (overrides or {}).items() if v is not None}}
    return DeployRequest(project_id=r["project_id"], model_key=r["model_key"], out_dir=Path(r["out_dir"]), plan=plan,
                         workspace_host=r["workspace_host"], warehouse_id=r["warehouse_id"],
                         allow_list=r["allow_list"], create_stubs=r["create_stubs"],
                         publish_incomplete=r["publish_incomplete"], stub_form=r["stub_form"],
                         staging=tuple(r["staging"]) if r.get("staging") else None,
                         deploy_principals=r["deploy_principals"], lease_run_id=lease_run_id,
                         run_id=intent["run_id"])
