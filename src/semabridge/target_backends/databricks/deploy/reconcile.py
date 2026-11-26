"""Reconciling deploy operations whose outcome is unknown (Phase 3.8a).

After a crash, timeout or lost connection, journal entries can be left
``intended`` / ``submitted`` / ``uncertain``. Nothing is retried or cleaned up
blindly. For each entry, read-only:

1. **Statement evidence first.** With a Databricks statement id, its status is
   read. Without one, query history is searched for the exact statement text (by
   hash) run by the deploying identity on the warehouse since the run started --
   positive evidence only: not finding it proves nothing.
2. Still PENDING / RUNNING / QUEUED -> left open (never "not executed" while the
   statement may still run).
3. SUCCEEDED -> the object must show the expected result AND the deploying
   identity as owner; then the operation is done and the object is recorded as
   ours with its Unity Catalog id. Otherwise uncertain.
4. FAILED / CANCELED -> not executed if the object is in its previous state;
   otherwise uncertain.
5. No evidence -> uncertain (a person decides). A matching definition, owner or
   run-marker property on its own never establishes ownership after an
   ambiguous creation. The one exception is a staging DROP whose object is gone:
   nothing remains to own.

Staging views confirmed as created by this run (statement evidence + identity)
are listed for 3.8b to drop.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from semabridge.target_backends.databricks.deploy import definitions as D
from semabridge.target_backends.databricks.deploy import statements as S
from semabridge.target_backends.databricks.deploy.live import Live

RUNNING_STATES = {"PENDING", "RUNNING", "QUEUED", "STARTED"}
SUCCESS_STATES = {"SUCCEEDED", "FINISHED"}
FAILURE_STATES = {"FAILED", "CANCELED", "CANCELLED", "CLOSED"}


def _live_hash(live: Live, info: Optional[Dict[str, Any]], fqn: str) -> Optional[str]:
    body = D.live_body(live, fqn, info)
    try:
        return D.definition_hash(body) if body else None
    except D.DefinitionError:
        return None


def _evidence(live: Live, op: Dict[str, Any], run: Dict[str, Any]) -> Tuple[Optional[str], str]:
    if op.get("statement_id"):
        status = live.statement_status(op["statement_id"])
        if status:
            return status, "statement status"
    if run.get("started_at") is not None:
        status = live.history_status(op["statement_sha256"], since=run["started_at"],
                                     warehouse_id=run.get("warehouse_id"), user=run.get("identity"))
        if status:
            return status, "query history"
    return None, "no statement evidence"


def _owner_ok(info: Optional[Dict[str, Any]], run: Dict[str, Any]) -> bool:
    return bool(info and run.get("identity") and str(info.get("owner") or "").lower() == str(run["identity"]).lower())


def _decide(live: Live, op: Dict[str, Any], run: Dict[str, Any]) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    kind, fqn = op["kind"], op["target_fqn"]
    info = live.table_info(fqn)
    status, source = _evidence(live, op, run)
    if status in RUNNING_STATES:
        return "open", f"the statement is still {status.lower()} ({source})", info
    if kind == S.DROP_STAGING:
        if info is None:
            return "succeeded", "the staging view is gone", info
        return ("not_executed", f"the staging view is still there ({source})", info) if status != "SUCCEEDED" else (
            "uncertain", "the drop reported success but the view is still there", info)
    if status is None:
        return "uncertain", "no statement evidence: a person must decide (object state alone proves nothing)", info
    if status in FAILURE_STATES:
        if kind in (S.CREATE_VIEW, S.CREATE_STAGING, S.STUB_TABLE) and info is None:
            return "not_executed", f"the statement {status.lower()} and the object does not exist", info
        if kind == S.ALTER_VIEW and info is not None and _live_hash(live, info, fqn) == op.get("previous_hash"):
            return "not_executed", f"the statement {status.lower()} and the view is unchanged", info
        return "uncertain", f"the statement {status.lower()} but the object is not in its previous state", info
    if status in SUCCESS_STATES:
        if info is None or not info.get("id"):
            return "uncertain", "the statement succeeded but the object or its id cannot be read", info
        if not _owner_ok(info, run):
            return "uncertain", f"the statement succeeded but the object is owned by {info.get('owner')}", info
        if kind in (S.CREATE_VIEW, S.CREATE_STAGING, S.ALTER_VIEW):
            if _live_hash(live, info, fqn) != op.get("definition_hash"):
                return "uncertain", "the statement succeeded but the definition differs from the approved one", info
            return "succeeded", f"confirmed by {source}, owner and definition", info
        if kind == S.STUB_TABLE:
            if str((info.get("properties") or {}).get("semabridge.run")) != str(op["run_id"]):
                return "uncertain", "the statement succeeded but the table lacks this run's marker", info
            return "succeeded", f"confirmed by {source}, owner and run marker", info
    return "uncertain", f"unrecognised statement status {status!r}", info


def _stub_names(store: Any, run_id: str, fqn: str) -> List[Dict[str, Any]]:
    """The stub-table name rows a run's intent planned for the table ``fqn``."""
    intent = store.intent_for_run(run_id)
    preview = ((intent or {}).get("intent") or {}).get("preview") or {}
    datasets = {t.get("name") for t in preview.get("stub_tables") or [] if str(t.get("fqn")) == fqn}
    return [n for n in preview.get("names_to_record") or []
            if n.get("kind") == "stub_table" and n.get("view_source") in datasets]


def reconcile(destination: str, live: Live, *, lease_run_id: str, store: Any = None) -> Dict[str, Any]:
    if store is None:
        from semabridge.target_backends.databricks.deploy import store as store_mod

        store = store_mod
    if not store.holds_lease(destination, lease_run_id):
        return {"status": "lease_not_held", "operations": [], "staging_to_drop": []}
    results: List[Dict[str, Any]] = []
    staging: List[str] = []
    runs: Dict[str, Dict[str, Any]] = {}
    for op in store.open_operations(destination):
        run = runs.setdefault(op["run_id"], store.run_info(op["run_id"]) or {})
        try:
            outcome, why, info = _decide(live, op, run)
        except Exception as exc:
            results.append({**op, "outcome": "uncertain", "why": f"could not read {op['target_fqn']}: {exc}"})
            continue
        if outcome != "open":
            store.journal_update(op["run_id"], op["seq"], outcome, error=why if outcome == "uncertain" else None)
        if outcome == "succeeded" and op["kind"] == S.CREATE_STAGING:
            staging.append(op["target_fqn"])
        if outcome == "succeeded" and op["kind"] in (S.CREATE_VIEW, S.ALTER_VIEW, S.STUB_TABLE) and run:
            kind = "stub_table" if op["kind"] == S.STUB_TABLE else "view"
            store.record_object(run_id=op["run_id"], project_id=run["project_id"], model_key=run["model_key"],
                                kind=kind, fqn=op["target_fqn"],
                                outcome="reconciled", uc_object_id=(info or {}).get("id"),
                                definition_hash=op.get("definition_hash"),
                                details={"comment": (info or {}).get("comment")})
            from semabridge.target_backends.databricks.deploy.execute import names_for

            names = (_stub_names(store, op["run_id"], op["target_fqn"]) if kind == "stub_table"
                     else names_for(store, op["run_id"], op["target_fqn"], "view", None))
            if names:
                store.record_names(run_id=op["run_id"], project_id=run["project_id"], model_key=run["model_key"],
                                   names=names)
        results.append({**op, "outcome": outcome, "why": why})
    # A run interrupted while "running": partial (something landed), failed (nothing
    # landed) or uncertain (a person must look / statements still running).
    for run_id in sorted({r["run_id"] for r in results}):
        run = runs.get(run_id) or {}
        if run.get("status") not in ("running", "uncertain"):
            continue
        mine = [r for r in results if r["run_id"] == run_id]
        if any(r["outcome"] in ("open", "uncertain") for r in mine):
            status = "uncertain"
        else:
            states = {j["state"] for j in store.journal(run_id)}
            status = "partial" if "succeeded" in states else "failed"
        store.finish_run(run_id, status, {"reconciled": [{k: r[k] for k in ("seq", "kind", "target_fqn", "outcome")}
                                                         for r in mine]})
    # Staging copies a stopped run definitely created (statement succeeded) but never
    # dropped: ours if the object is still there, owned by that run's identity, with
    # the definition that statement set.
    for left_op in store.leftover_staging(destination):
        run = runs.setdefault(left_op["run_id"], store.run_info(left_op["run_id"]) or {})
        try:
            info = live.table_info(left_op["target_fqn"])
        except Exception:
            continue
        if info is None:
            continue
        if _owner_ok(info, run) and _live_hash(live, info, left_op["target_fqn"]) == left_op.get("definition_hash"):
            staging.append(left_op["target_fqn"])
        else:
            results.append({**left_op, "kind": S.CREATE_STAGING, "outcome": "uncertain",
                            "why": "a leftover staging copy does not match what this run created; left in place"})
    left = [r for r in results if r["outcome"] in ("uncertain", "open")]
    return {"status": "needs_attention" if left else "reconciled", "operations": results,
            "staging_to_drop": sorted(set(staging))}
