"""Reconciling deploy operations whose outcome is unknown (Phase 3.8a).

After a crash, timeout or lost connection, journal entries can be left
``intended`` / ``submitted`` / ``uncertain``. Nothing is retried or cleaned up
blindly: each entry is checked against what Databricks actually has, read-only,
and the journal is corrected. Only then may another deploy touch the schema.

Outcomes per operation:
* succeeded    -- the object is there in exactly the state the statement sets
                  (created objects are then recorded as SemaBridge's own);
* not_executed -- the object is in the state from before the statement;
* uncertain    -- anything else (left for a person: never overwritten).
Staging views found to exist are listed for cleanup; 3.8b drops them, and only
those (their ownership is established by this journal + their identity).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from semabridge.target_backends.databricks.deploy import definitions as D
from semabridge.target_backends.databricks.deploy import statements as S
from semabridge.target_backends.databricks.deploy.live import Live


def _live_hash(live: Live, info: Optional[Dict[str, Any]], fqn: str) -> Optional[str]:
    body = D.extract_yaml((info or {}).get("view_definition")) or D.extract_yaml(live.show_create(fqn))
    return D.definition_hash(body) if body else None


def reconcile(destination: str, live: Live, *, lease_run_id: str, store: Any = None) -> Dict[str, Any]:
    if store is None:
        from semabridge.target_backends.databricks.deploy import store as store_mod

        store = store_mod
    if not store.holds_lease(destination, lease_run_id):
        return {"status": "lease_not_held", "operations": [], "staging_to_drop": []}
    results: List[Dict[str, Any]] = []
    staging: List[str] = []
    for op in store.open_operations(destination):
        kind, fqn = op["kind"], op["target_fqn"]
        try:
            info = live.table_info(fqn)
        except Exception as exc:
            results.append({**op, "outcome": "uncertain", "why": f"could not read {fqn}: {exc}"})
            continue
        outcome, why = "uncertain", ""
        if kind in (S.CREATE_VIEW, S.CREATE_STAGING):
            if info is None:
                outcome = "not_executed"
            elif _live_hash(live, info, fqn) == op.get("definition_hash"):
                outcome = "succeeded"
                if kind == S.CREATE_STAGING:
                    staging.append(fqn)
            else:
                why = "an object with this name exists with a different definition"
        elif kind == S.ALTER_VIEW:
            h = _live_hash(live, info, fqn) if info is not None else None
            if h is not None and h == op.get("definition_hash"):
                outcome = "succeeded"
            elif h is not None and h == op.get("previous_hash"):
                outcome = "not_executed"
            else:
                why = "the live definition is neither the old nor the new one"
        elif kind == S.STUB_TABLE:
            if info is None:
                outcome = "not_executed"
            elif str((info.get("properties") or {}).get("semabridge.stub")).lower() == "true" and \
                    str((info.get("properties") or {}).get("semabridge.run")) == str(op["run_id"]):
                outcome = "succeeded"
            else:
                why = "a table with this name exists but was not created by this run"
        elif kind == S.DROP_STAGING:
            outcome = "succeeded" if info is None else "not_executed"
            if info is not None:
                staging.append(fqn)
        state = outcome if outcome != "uncertain" else "uncertain"
        store.journal_update(op["run_id"], op["seq"], state, error=why or None)
        if outcome == "succeeded" and kind in (S.CREATE_VIEW, S.ALTER_VIEW, S.STUB_TABLE):
            run = store.run_info(op["run_id"]) or {}
            if run:
                store.record_object(run_id=op["run_id"], project_id=run["project_id"], model_key=run["model_key"],
                                    kind="stub_table" if kind == S.STUB_TABLE else "view", fqn=fqn,
                                    outcome="reconciled", uc_object_id=(info or {}).get("id"),
                                    definition_hash=op.get("definition_hash"))
        results.append({**op, "outcome": outcome, "why": why})
    # A run interrupted while "running": after reconciliation it is partial (something
    # landed), failed (nothing landed) or uncertain (a person must look).
    for run_id in sorted({r["run_id"] for r in results}):
        run = store.run_info(run_id) or {}
        if run.get("status") != "running":
            continue
        mine = [r for r in results if r["run_id"] == run_id]
        status = ("uncertain" if any(r["outcome"] == "uncertain" for r in mine) else
                  "partial" if any(r["outcome"] == "succeeded" for r in mine) else "failed")
        store.finish_run(run_id, status, {"reconciled": [{k: r[k] for k in ("seq", "kind", "target_fqn", "outcome")}
                                                         for r in mine]})
    left = [r for r in results if r["outcome"] == "uncertain"]
    return {"status": "needs_attention" if left else "reconciled", "operations": results,
            "staging_to_drop": sorted(set(staging))}
