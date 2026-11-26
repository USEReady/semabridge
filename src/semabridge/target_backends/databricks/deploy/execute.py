"""Phase 3.8b: executing a confirmed deployment intent.

Order (design v2 + reviews):

1. Confirm: the stored intent is consumed and its run row created in ONE
   transaction; a repeated confirmation returns the existing run.
2. Leases on the destination and the staging schema, acquired in a fixed
   (sorted) order; if the second cannot be had, the first is released.
3. ``prepare`` again under the leases with the intent's run id and token. A
   different fingerprint means something changed since the preview: nothing is
   written, the run ends ``superseded``.
4. Stub tables (only when chosen), then each view: staging copy (restricted
   schema) -> read-only structural validation -> ownership/drift re-check right
   before promotion -> CREATE / ALTER -> read-back -> drop staging -> record.
5. Every write: leases renewed first (lost -> stop), journaled ``intended``,
   sent through the run's exact-statement guard, journaled with its outcome.

Failures:
* LOCAL (Databricks definitively refused a statement; validation failed; the
  pre-promotion re-check failed; a stub read-back mismatched): that object fails
  -- its staging copy is dropped -- and the others continue.
* RUN-WIDE (lease lost, guard refusal, store unavailable) or an UNCERTAIN write
  (timeout, lost connection, cancelled/closed, auth failure mid-statement):
  every further write stops at once, cleanup included. Reconcile next.
* A read-back that disagrees (definition, owner, identity, grants, comment) ->
  ``applied_unverified``: reported with its evidence, never fixed automatically;
  verified state and name history are NOT advanced until Re-verify or
  Reconcile succeeds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from semabridge.target_backends.databricks.deploy import definitions as D
from semabridge.target_backends.databricks.deploy import statements as S
from semabridge.target_backends.databricks.deploy.intent import fingerprint, rebuild_request
from semabridge.target_backends.databricks.deploy.live import Live

logger = logging.getLogger(__name__)


class DeployStop(RuntimeError):
    """Run-wide stop: no further writes."""

    def __init__(self, reason: str, *, uncertain: bool) -> None:
        super().__init__(reason)
        self.uncertain = uncertain


class LocalFailure(RuntimeError):
    """One object failed definitively; the run continues."""


@dataclass
class ObjectResult:
    kind: str
    name: str
    fqn: str
    outcome: str                     # created | updated | unchanged | verified | applied_unverified | failed | skipped
    detail: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "name": self.name, "fqn": self.fqn, "outcome": self.outcome,
                "detail": self.detail, "evidence": dict(self.evidence)}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _statement_state(exc: Exception) -> str:
    """The terminal state in a DatabricksStatementError ("Statement <id> <STATE>: ...")."""
    import re

    m = re.match(r"\s*Statement\s+\S*\s+([A-Z_]+):", str(exc))
    return m.group(1) if m else "UNKNOWN"


def _yaml_comment(body: str) -> Optional[str]:
    try:
        doc = D.canonical(body)
    except D.DefinitionError:
        return None
    c = doc.get("comment")
    return c if isinstance(c, str) else None


class DeployExecution:
    def __init__(self, intent: Dict[str, Any], *, plan: Dict[str, Any], live: Live,
                 write_client: Callable[[Any], Any], store: Any = None,
                 clock: Callable[[], datetime] = _now, overrides: Optional[Dict[str, Any]] = None) -> None:
        if store is None:
            from semabridge.target_backends.databricks.deploy import store as store_mod

            store = store_mod
        self.intent, self.plan, self.live, self.make_writer, self.store = intent, plan, live, write_client, store
        self.clock = clock
        self.run_id = intent["run_id"]
        self.results: List[ObjectResult] = []
        self.keys: List[str] = []
        self.stopped: Optional[DeployStop] = None
        self.writer = None
        self.identity: Optional[str] = None
        self.dest_key = self.stg_key = ""
        self.overrides = dict(overrides or {})

    # --- the run ------------------------------------------------------------------------------------

    def run(self) -> Dict[str, Any]:
        from semabridge.target_backends.databricks.deploy.prepare import prepare

        preview = self.intent["intent"]["preview"]
        self.dest_key, self.stg_key = preview.get("lease_key") or "", preview.get("staging_lease_key") or ""
        try:
            if not self._acquire([self.dest_key, self.stg_key]):
                return self._finish("failed", {"reason": "another deploy holds the schema lease; try again later"})
            bound_wh = self.intent["intent"]["request"].get("warehouse_id")
            live_wh = getattr(self.live, "warehouse_id", None)
            if live_wh and bound_wh and str(live_wh) != str(bound_wh):
                return self._finish("superseded", {"reason": f"the SQL warehouse is now {live_wh}, not {bound_wh} as "
                                                             "previewed: preview again"})
            # current project settings (allow-list, staging, principals, stub form) -- a
            # tightened setting changes the fingerprint and the run is superseded
            req = rebuild_request(self.intent, plan=self.plan, lease_run_id=self.run_id, overrides=self.overrides)
            pv = prepare(req, self.live, store=self.store, now=self.clock(), token=self.intent["intent"]["token"])
            fp = fingerprint(pv, req)
            if fp != self.intent["fingerprint"] or pv.status not in ("ready", "no_op"):
                return self._finish("superseded", {"reason": "the situation changed since the preview: preview again",
                                                   "new_preview": pv.as_dict()})
            self.identity = (pv.platform or {}).get("identity")
            self.pv = pv
            self.writer = self.make_writer(pv.write_plan()) if pv.statements else None
            try:
                self._execute(pv)
            except DeployStop as stop:
                self.stopped = stop
                logger.warning("Databricks deploy %s stopped: %s", self.run_id, stop)
            if self.stopped is None:                         # after a stop: no further reads or records
                try:
                    self._record_verified(pv)
                except Exception as exc:
                    logger.warning("unchanged objects of %s could not be re-verified: %s", self.run_id, exc)
            return self._finish(self._status(pv), {})
        except Exception as exc:  # store / prepare failure: nothing more is written
            logger.exception("Databricks deploy %s failed", self.run_id)
            if self.stopped is None or not self.stopped.uncertain:     # never downgrade an uncertain stop
                self.stopped = DeployStop(f"{type(exc).__name__}: {exc}", uncertain=False)
            return self._finish("uncertain" if self.stopped.uncertain else "failed", {"reason": str(exc)})
        finally:
            for key in reversed(self.keys):
                try:
                    self.store.release_lease(key, self.run_id)
                except Exception:
                    logger.warning("could not release lease %s", key)

    def _acquire(self, keys: List[str]) -> bool:
        for key in sorted(k for k in set(keys) if k):          # fixed order: no deadlock between two runs
            if not self.store.acquire_lease(key, self.run_id):
                for held in reversed(self.keys):
                    self.store.release_lease(held, self.run_id)
                self.keys = []
                return False
            self.keys.append(key)
        return True

    # --- statements -----------------------------------------------------------------------------------

    def _write(self, seq: int, st: S.Statement, *, definition_hash: Optional[str] = None,
               previous_hash: Optional[str] = None) -> Any:
        from semabridge.target_backends.databricks.client import DatabricksStatementError
        from semabridge.target_backends.databricks.guard import DatabricksGuardError

        for key in self.keys:                                    # never write without the leases
            if not self.store.renew_lease(key, self.run_id):
                raise DeployStop(f"lease {key} was lost before {st.kind} {st.target}", uncertain=False)
        dest = self.stg_key if st.kind in (S.CREATE_STAGING, S.DROP_STAGING) else self.dest_key
        self.store.journal_intend(run_id=self.run_id, seq=seq, destination=dest, kind=st.kind, target_fqn=st.target,
                                  statement_sha256=st.sha256, definition_hash=definition_hash,
                                  previous_hash=previous_hash)
        try:
            res = self.writer.execute(st.sql)
        except DatabricksGuardError as exc:                     # never sent: a defect
            self.store.journal_update(self.run_id, seq, "not_executed", error=f"guard: {exc}")
            raise DeployStop(f"the guard refused {st.kind} {st.target} (defect): {exc}", uncertain=False) from None
        except DatabricksStatementError as exc:
            if _statement_state(exc) == "FAILED":                # ran and failed: definite, nothing changed
                self.store.journal_update(self.run_id, seq, "failed", statement_id=exc.statement_id, error=str(exc))
                raise LocalFailure(f"Databricks refused {st.kind} {st.target}: {exc}") from None
            self.store.journal_update(self.run_id, seq, "uncertain", statement_id=exc.statement_id, error=str(exc))
            raise DeployStop(f"{st.kind} {st.target} ended without a definite outcome: {exc}", uncertain=True) from None
        except Exception as exc:                                 # timeout, connection, auth mid-statement
            self.store.journal_update(self.run_id, seq, "uncertain", statement_id=getattr(exc, "statement_id", None),
                                      error=f"{type(exc).__name__}: {exc}")
            raise DeployStop(f"{st.kind} {st.target}: outcome unknown ({type(exc).__name__})", uncertain=True) from None
        self.store.journal_update(self.run_id, seq, "succeeded", statement_id=getattr(res, "statement_id", None))
        return res

    # --- execution --------------------------------------------------------------------------------------

    def _execute(self, pv) -> None:
        seqs = {id(st): n for n, st in enumerate(pv.statements, 1)}
        failed_stubs: set = set()
        stub_items = {i.fqn: i for i in pv.stub_tables}
        for st in [s for s in pv.statements if s.kind == S.STUB_TABLE]:
            item = stub_items.get(st.target)
            try:
                self._write(seqs[id(st)], st)
                self._guarded_readback(seqs[id(st)], st, lambda: self._readback_stub(st, item))
            except LocalFailure as exc:
                failed_stubs.add(item.name if item else st.target)
                self.results.append(ObjectResult("stub_table", item.name if item else st.target, st.target,
                                                 "failed", str(exc)))
        groups: Dict[str, List[S.Statement]] = {}
        for st in pv.statements:
            if st.kind != S.STUB_TABLE:
                groups.setdefault(str(st.view), []).append(st)
        views = {v.name: v for v in pv.views}
        for view, stmts in groups.items():
            item = views[view]
            needs = set(item.details.get("stub_backed") or [])
            if needs & failed_stubs:
                self.results.append(ObjectResult("view", view, item.fqn, "skipped",
                                                 f"needs table(s) {sorted(needs & failed_stubs)} that failed"))
                continue
            self._deploy_view(item, stmts, seqs)

    def _deploy_view(self, item, stmts: List[S.Statement], seqs: Dict[int, int]) -> None:
        staging, promote, drop = stmts
        body = self._body(item.name)
        new_hash = D.definition_hash(body)
        prev = item.details.get("previous_hash")
        try:
            self._write(seqs[id(staging)], staging, definition_hash=new_hash)
        except LocalFailure as exc:
            self.results.append(ObjectResult("view", item.name, item.fqn, "failed", str(exc)))
            return
        try:
            self._validate_staging(item.name)
            self._recheck_before_promotion(item, new_hash)
            grants_before = self.live.show_grants(item.fqn) if promote.kind == S.ALTER_VIEW else None
            if promote.kind == S.ALTER_VIEW and grants_before is None:
                raise LocalFailure(f"the grants on {item.fqn} cannot be read, so the update could not be verified; "
                                   "not updated")
            info_before = self.live.table_info(item.fqn) if promote.kind == S.ALTER_VIEW else None
            self._write(seqs[id(promote)], promote, definition_hash=new_hash, previous_hash=prev)
        except LocalFailure as exc:
            self.results.append(ObjectResult("view", item.name, item.fqn, "failed", str(exc)))
            self._drop_staging(drop, seqs)                   # definite state: cleanup is safe
            return
        result = self._guarded_readback(
            seqs[id(promote)], promote,
            lambda: self._readback_view(item, promote, body, new_hash, grants_before, info_before))
        self.results.append(result)
        self._drop_staging(drop, seqs)

    def _guarded_readback(self, seq: int, st: S.Statement, fn: Callable[[], Any]) -> Any:
        """Read-back after a successful write. If it cannot even run (Databricks or
        the store unavailable), the object is recorded applied_unverified -- or, if
        that is impossible too, its journal entry becomes uncertain -- and the run
        stops, so Reconcile picks the object up. Never an unrecorded object."""
        try:
            return fn()
        except LocalFailure:
            raise
        except Exception as exc:
            why = f"read-back failed after {st.kind}: {type(exc).__name__}: {exc}"
            try:
                self.store.record_object(run_id=self.run_id, project_id=self.pv_project, model_key=self.pv_model,
                                         kind="stub_table" if st.kind == S.STUB_TABLE else "view", fqn=st.target,
                                         source=st.view, outcome="applied_unverified",
                                         details={"problems": [why], "expected": {}})
            except Exception:
                try:
                    self.store.journal_update(self.run_id, seq, "uncertain", error=why)
                except Exception:
                    logger.error("deploy %s: %s; could not record it either", self.run_id, why)
            raise DeployStop(why, uncertain=True) from None

    def _drop_staging(self, drop: S.Statement, seqs: Dict[int, int]) -> None:
        try:
            self._write(seqs[id(drop)], drop)
        except LocalFailure as exc:
            self.results.append(ObjectResult("staging_view", drop.view or "", drop.target, "failed",
                                             f"staging copy left in place: {exc}"))

    def _body(self, view: str) -> str:
        from semabridge.target_backends.databricks import bundle

        r = self.intent["intent"]["request"]
        folder = Path(r["out_dir"]) / bundle.VIEWS_DIR / str(self.intent["plan_revision"])
        return (folder / f"{view}.yaml").read_bytes().decode("utf-8")

    def _validate_staging(self, view: str) -> None:
        check = next((c for c in self.pv.checks if c["view"] == view), None)
        for sql in (check or {}).get("sql") or []:
            try:
                self.live.run_read(sql)
            except Exception as exc:
                raise LocalFailure(f"staging validation failed ({sql.split()[0]}): {exc}") from None

    def _recheck_before_promotion(self, item, new_hash: str) -> None:
        """Ownership and drift, immediately before CREATE / ALTER."""
        info = self.live.table_info(item.fqn)
        if item.action == "create":
            if info is not None:
                raise LocalFailure(f"{item.fqn} appeared since the preview; it is never replaced")
            return
        if info is None:
            raise LocalFailure(f"{item.fqn} disappeared since the preview")
        if not info.get("id") or info["id"] != item.details.get("uc_object_id"):
            raise LocalFailure(f"{item.fqn} is no longer the object SemaBridge deployed")
        if self.identity and str(info.get("owner") or "").lower() != str(self.identity).lower():
            raise LocalFailure(f"{item.fqn} is now owned by {info.get('owner')}")
        body = D.live_body(self.live, item.fqn, info)
        try:
            live_hash = D.definition_hash(body) if body else None
        except D.DefinitionError:
            live_hash = None
        if live_hash != item.details.get("previous_hash"):
            raise LocalFailure(f"{item.fqn} was changed since the preview (drift); it is not overwritten")

    # --- read-back --------------------------------------------------------------------------------------

    def _readback_view(self, item, promote: S.Statement, body: str, new_hash: str,
                       grants_before: Optional[List[Tuple[str, str]]],
                       info_before: Optional[Dict[str, Any]]) -> ObjectResult:
        update = promote.kind == S.ALTER_VIEW
        expected = {"definition_hash": new_hash, "owner": self.identity,
                    "uc_object_id": item.details.get("uc_object_id") if update else None,
                    "grants": grants_before if update else None,
                    "comments": sorted(c for c in {_yaml_comment(body),
                                                   (info_before or {}).get("comment")} if c),
                    # an absent object comment is acceptable only if there was none before
                    "comment_may_be_empty": not (info_before or {}).get("comment")}
        problems, observed = self._compare_view(item.fqn, expected, check_grants=update)
        names = [n for n in self.pv.names_to_record if n.get("view_name") == item.name]
        if problems:
            self.store.record_object(run_id=self.run_id, project_id=self.pv_project, model_key=self.pv_model,
                                     kind="view", fqn=item.fqn, source=item.name, outcome="applied_unverified",
                                     uc_object_id=observed.get("uc_object_id"), definition_hash=None,
                                     readiness=item.readiness,
                                     details={"expected": expected, "observed": observed, "problems": problems,
                                              "names": names, "update": update})
            return ObjectResult("view", item.name, item.fqn, "applied_unverified", "; ".join(problems),
                                {"expected": expected, "observed": observed})
        outcome = "updated" if update else "created"
        self.store.record_object(run_id=self.run_id, project_id=self.pv_project, model_key=self.pv_model,
                                 kind="view", fqn=item.fqn, source=item.name, outcome=outcome,
                                 uc_object_id=observed["uc_object_id"], definition_hash=new_hash,
                                 readiness=item.readiness, details={"comment": observed.get("comment")})
        if names:
            self.store.record_names(run_id=self.run_id, project_id=self.pv_project, model_key=self.pv_model,
                                    names=names)
        return ObjectResult("view", item.name, item.fqn, outcome, "read-back verified", {"observed": observed})

    def _compare_view(self, fqn: str, expected: Dict[str, Any], *, check_grants: bool
                      ) -> Tuple[List[str], Dict[str, Any]]:
        problems: List[str] = []
        try:
            info = self.live.table_info(fqn)
        except Exception as exc:
            return [f"read-back failed: {exc}"], {}
        if info is None:
            return ["the view cannot be read back"], {}
        body = D.live_body(self.live, fqn, info)
        try:
            live_hash = D.definition_hash(body) if body else None
        except D.DefinitionError:
            live_hash = None
        observed = {"uc_object_id": info.get("id"), "owner": info.get("owner"), "definition_hash": live_hash,
                    "comment": info.get("comment")}
        if not info.get("id"):
            problems.append("no Unity Catalog object id")
        if expected.get("uc_object_id") and info.get("id") != expected["uc_object_id"]:
            problems.append("the object id changed")
        if expected.get("owner") and str(info.get("owner") or "").lower() != str(expected["owner"]).lower():
            problems.append(f"owner is {info.get('owner')}")
        if live_hash != expected["definition_hash"]:
            problems.append("the definition read back differs from the approved one")
        comment = info.get("comment")
        if comment is None or comment == "":
            if not expected.get("comment_may_be_empty", True):
                problems.append("the object comment was removed")
        elif expected.get("comments") and comment not in expected["comments"]:
            problems.append("unexpected object comment")
        if check_grants:
            after = self.live.show_grants(fqn)
            observed["grants"] = after
            if expected.get("grants") is None or after is None:
                problems.append("grants could not be compared")
            elif sorted(after) != sorted(expected["grants"]):
                problems.append("grants differ after the update")
        return problems, observed

    def _readback_stub(self, st: S.Statement, item) -> None:
        info = self.live.table_info(st.target)
        expected_cols = {str(c["name"]).lower(): "".join(str(c["type"]).lower().split())
                         for c in (item.details.get("columns") if item else []) or []}
        problems = []
        if info is None:
            problems.append("cannot be read back")
        else:
            props = info.get("properties") or {}
            if not info.get("id"):
                problems.append("no Unity Catalog object id")
            if self.identity and str(info.get("owner") or "").lower() != str(self.identity).lower():
                problems.append(f"owner is {info.get('owner')}")
            if str(props.get("semabridge.stub")).lower() != "true" or str(props.get("semabridge.run")) != self.run_id:
                problems.append("stub marker properties missing")
            got = {str(c.get("name") or "").lower(): "".join(str(c.get("type") or "").lower().split())
                   for c in info.get("columns") or []}
            if got != expected_cols:
                problems.append("columns or types differ from the approved definition")
        if problems:
            self.store.record_object(run_id=self.run_id, project_id=self.pv_project, model_key=self.pv_model,
                                     kind="stub_table", fqn=st.target, source=item.name if item else None,
                                     outcome="applied_unverified", uc_object_id=(info or {}).get("id"),
                                     details={"problems": problems, "expected_columns": expected_cols})
            raise LocalFailure(f"stub table {st.target} read-back: {'; '.join(problems)}")
        self.store.record_object(run_id=self.run_id, project_id=self.pv_project, model_key=self.pv_model,
                                 kind="stub_table", fqn=st.target, source=item.name if item else None,
                                 outcome="created", uc_object_id=info["id"], readiness="incomplete_unverified",
                                 details={"properties": dict(info.get("properties") or {})})
        names = [n for n in self.pv.names_to_record if n.get("kind") == "stub_table"
                 and item is not None and n.get("view_source") == item.name]
        if names:
            self.store.record_names(run_id=self.run_id, project_id=self.pv_project, model_key=self.pv_model,
                                    names=names)
        self.results.append(ObjectResult("stub_table", item.name if item else st.target, st.target, "created",
                                         "read-back verified"))

    # --- bookkeeping ------------------------------------------------------------------------------------

    @property
    def pv_project(self) -> str:
        return self.intent["project_id"]

    @property
    def pv_model(self) -> str:
        return self.intent["model_key"]

    def _record_verified(self, pv) -> None:
        """Unchanged objects: re-recorded as verified (read-only evidence from the
        re-check under the leases). Their names are re-confirmed too."""
        for o in pv.verified_objects:
            self.store.record_object(run_id=self.run_id, project_id=self.pv_project, model_key=self.pv_model,
                                     kind=o["kind"], fqn=o["fqn"], source=o.get("source"), outcome="verified",
                                     uc_object_id=o.get("uc_object_id"), definition_hash=o.get("definition_hash"),
                                     details={"comment": (self.live.table_info(o["fqn"]) or {}).get("comment")}
                                     if o["kind"] == "view" else {})
            self.results.append(ObjectResult(o["kind"], o.get("source") or o["fqn"], o["fqn"], "unchanged",
                                             "verified, no change needed"))
        unchanged = {o.get("source") for o in pv.verified_objects if o["kind"] == "view"}
        names = [n for n in pv.names_to_record if n.get("view_name") in unchanged]
        if names:
            self.store.record_names(run_id=self.run_id, project_id=self.pv_project, model_key=self.pv_model,
                                    names=names)

    def _status(self, pv) -> str:
        work = [r for r in self.results if r.kind in ("view", "stub_table") and r.outcome != "unchanged"]
        good = [r for r in work if r.outcome in ("created", "updated")]
        if self.stopped is not None:
            if self.stopped.uncertain:
                return "uncertain"
            return "partial" if good else "failed"
        if not work and not any(r.kind == "staging_view" for r in self.results):
            return "no_op"
        leftovers = [r for r in self.results if r.kind == "staging_view" and r.outcome == "failed"]
        if work and len(good) == len(work) and not leftovers:
            return "succeeded"
        return "partial" if good or any(r.outcome == "applied_unverified" for r in work) else "failed"

    def _finish(self, status: str, extra: Dict[str, Any]) -> Dict[str, Any]:
        summary = {"run_id": self.run_id, "status": status, "results": [r.as_dict() for r in self.results],
                   "stopped": str(self.stopped) if self.stopped else None, **extra}
        if status == "failed" and not extra.get("reason") and self.results and not self.stopped:
            summary["error"] = "DeploymentError: there was work to do and nothing succeeded"
        try:
            self.store.finish_run(self.run_id, status, summary)
        except Exception:
            logger.exception("could not record the end of deploy run %s", self.run_id)
        try:
            summary["journal"] = self.store.journal(self.run_id)
        except Exception:
            summary["journal"] = []
        return summary


# --- confirmation, re-verify, reconcile ------------------------------------------------------------

class IntentError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def confirm_intent(intent_id: str, *, project_id: str, model_key: str, user_id: Optional[str],
                   acknowledge_destination_access: bool, plan: Dict[str, Any], current_revision: Optional[int],
                   live: Live, write_client: Callable[[Any], Any], store: Any = None,
                   overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Execute a stored intent (at most once). Returns the run summary, or
    {"run_id", "status": "existing"} for a repeated confirmation."""
    if store is None:
        from semabridge.target_backends.databricks.deploy import store as store_mod

        store = store_mod
    intent = store.get_intent(intent_id)
    if intent is None or intent["project_id"] != str(project_id) or intent["model_key"] != str(model_key or ""):
        raise IntentError("intent_not_found", "no such deployment intent for this project")
    if intent["status"] == "used":
        return {"run_id": intent["run_id"], "status": "existing", "run": store.run_info(intent["run_id"])}
    if intent["status"] == "open" and current_revision is not None and int(current_revision) != intent["plan_revision"]:
        store.set_intent_status(intent_id, "invalidated")
        raise IntentError("intent_invalidated", "the plan changed since this preview: preview again")
    if intent["intent"].get("requires_access_acknowledgement") and not acknowledge_destination_access:
        raise IntentError("acknowledgement_required",
                          "incomplete views are included: acknowledge who can read the destination")
    p = intent["intent"]["preview"]
    plat, dest = p.get("platform") or {}, p.get("destination") or {}
    from semabridge.target_backends.databricks.writer import WRITER_REVISION

    res = store.consume_intent(intent_id, used_by=user_id, run={
        "writer_revision": WRITER_REVISION, "dbsql_version": plat.get("dbsql_version"),
        "metastore_id": plat.get("metastore"), "catalog": dest.get("catalog"), "schema": dest.get("schema"),
        "workspace_host": plat.get("workspace_host"), "warehouse_id": plat.get("warehouse_id"),
        "identity": plat.get("identity"), "stub_form": p.get("stub_form")})
    if res["outcome"] == "existing":
        return {"run_id": res["run_id"], "status": "existing", "run": store.run_info(res["run_id"])}
    if res["outcome"] != "started":
        raise IntentError(f"intent_{res['outcome']}", f"this preview is {res['outcome']}: preview again")
    intent = store.get_intent(intent_id)
    return DeployExecution(intent, plan=plan, live=live, write_client=write_client, store=store,
                           overrides=overrides).run()


def names_for(store: Any, run_id: Optional[str], fqn: str, kind: str, source: Optional[str]) -> List[Dict[str, Any]]:
    """The names a run's intent planned to record for one object (used when Reconcile
    or Re-verify confirms an object the run itself could not verify)."""
    intent = store.intent_for_run(run_id) if run_id else None
    rows = ((intent or {}).get("intent") or {}).get("preview", {}).get("names_to_record") or []
    if kind == "stub_table":
        return [n for n in rows if n.get("kind") == "stub_table" and n.get("view_source") == source]
    view = fqn.split(".")[-1]
    return [n for n in rows if str(n.get("view_name") or "").lower() == view]


def reverify(project_id: str, model_key: str, live: Live, *, store: Any = None, run_id: Optional[str] = None
             ) -> Dict[str, Any]:
    """Read-only: re-check objects whose latest state is applied_unverified against
    the ORIGINAL approved evidence; only when every check passes is the verified
    state (and the view's names) recorded."""
    import uuid

    if store is None:
        from semabridge.target_backends.databricks.deploy import store as store_mod

        store = store_mod
    run_id = run_id or f"reverify-{uuid.uuid4().hex[:16]}"
    results = []
    for fqn, rec in sorted(store.owned_objects(project_id, model_key).items()):
        if rec.get("outcome") != "applied_unverified":
            continue
        det = rec.get("details") or {}
        exp = det.get("expected") or {}
        problems: List[str] = []
        info = live.table_info(fqn)
        if info is None:
            problems.append("cannot be read")
        elif not rec.get("uc_object_id") or info.get("id") != rec["uc_object_id"]:
            problems.append("no recorded Unity Catalog id matches the live object: ownership cannot be established "
                            "(Reconcile or a person must decide)")
        elif rec["kind"] == "view":
            body = D.live_body(live, fqn, info)
            try:
                h = D.definition_hash(body) if body else None
            except D.DefinitionError:
                h = None
            if not info.get("id"):
                problems.append("no Unity Catalog object id")
            if exp.get("uc_object_id") and info.get("id") != exp["uc_object_id"]:
                problems.append("the object id differs from the approved one")
            if exp.get("owner") and str(info.get("owner") or "").lower() != str(exp["owner"]).lower():
                problems.append(f"owner is {info.get('owner')}")
            if h != exp.get("definition_hash"):
                problems.append("definition differs from the approved one")
            comment = info.get("comment")
            if not comment:
                if not exp.get("comment_may_be_empty", True):
                    problems.append("the object comment was removed")
            elif exp.get("comments") and comment not in exp["comments"]:
                problems.append("unexpected object comment")
            if det.get("update"):
                g = live.show_grants(fqn)
                if g is None or exp.get("grants") is None or sorted(map(tuple, g)) != sorted(map(tuple, exp["grants"])):
                    problems.append("grants differ from those before the update")
        else:                                                         # an empty stub table
            run = store.run_info(rec.get("run_id")) or {}
            props = info.get("properties") or {}
            if run.get("identity") and str(info.get("owner") or "").lower() != str(run["identity"]).lower():
                problems.append(f"owner is {info.get('owner')}")
            if str(props.get("semabridge.stub")).lower() != "true" or str(props.get("semabridge.run")) != rec.get("run_id"):
                problems.append("stub marker properties missing")
            got = {str(c.get("name") or "").lower(): "".join(str(c.get("type") or "").lower().split())
                   for c in info.get("columns") or []}
            if got != (det.get("expected_columns") or {}):
                problems.append("columns or types differ from the approved definition")
            if live.has_rows(fqn) is not False:
                problems.append("the table is not verifiably empty")
        if problems:
            results.append({"fqn": fqn, "outcome": "still_unverified", "problems": problems})
            continue
        if not store.run_info(run_id):
            store.start_run(run_id=run_id, project_id=project_id, model_key=model_key, plan_revision=0,
                            writer_revision="", catalog=fqn.split(".")[0], schema=fqn.split(".")[1],
                            stub_form="empty_table")
        store.record_object(run_id=run_id, project_id=project_id, model_key=model_key, kind=rec["kind"], fqn=fqn,
                            source=rec.get("source"), outcome="verified", uc_object_id=info.get("id"),
                            definition_hash=exp.get("definition_hash"), readiness=rec.get("readiness"),
                            details={"comment": info.get("comment"), "reverified_from": rec.get("run_id")}
                            if rec["kind"] == "view" else {"properties": dict(info.get("properties") or {})})
        names = det.get("names") or names_for(store, rec.get("run_id"), fqn, rec["kind"], rec.get("source"))
        if names:
            store.record_names(run_id=run_id, project_id=project_id, model_key=model_key, names=names)
        results.append({"fqn": fqn, "outcome": "verified"})
    if store.run_info(run_id):
        store.finish_run(run_id, "succeeded", {"reverified": results})
    return {"results": results}


def reconcile_and_clean(lease_keys: List[str], live: Live, write_client: Callable[[Any], Any], *,
                        store: Any = None) -> Dict[str, Any]:
    """The Reconcile action: leases (sorted order) -> reconcile each schema ->
    drop ONLY the staging views reconcile confirmed as ours -> release."""
    import uuid

    from semabridge.target_backends.databricks.deploy.reconcile import reconcile

    if store is None:
        from semabridge.target_backends.databricks.deploy import store as store_mod

        store = store_mod
    holder = f"reconcile-{uuid.uuid4().hex[:16]}"
    held: List[str] = []
    for key in sorted(k for k in set(lease_keys) if k):
        if not store.acquire_lease(key, holder):
            for h in reversed(held):
                store.release_lease(h, holder)
            return {"status": "busy", "detail": "a deploy holds the schema lease; try again later"}
        held.append(key)
    out: Dict[str, Any] = {"status": "reconciled", "schemas": {}, "dropped": [], "drop_errors": []}
    try:
        staging: List[str] = []
        for key in held:
            res = reconcile(key, live, lease_run_id=holder, store=store)
            out["schemas"][key] = res
            staging += res.get("staging_to_drop") or []
            if res.get("status") != "reconciled":
                out["status"] = "needs_attention"
        if staging:
            from semabridge.target_backends.databricks.client import DatabricksStatementError

            drops = [S.drop_staging_fqn(f) for f in sorted(set(staging))]
            writer = write_client(S.write_plan(drops))
            store.start_run(run_id=holder, project_id="", model_key="", plan_revision=0, writer_revision="",
                            catalog="", schema="", stub_form="empty_table")
            for n, st in enumerate(drops, 1):
                key = next((k for k in held if st.target.startswith(k.split("|", 1)[1].replace("|", ".") + ".")),
                           held[0])
                if not all(store.renew_lease(k, holder) for k in held):
                    out["status"] = "needs_attention"
                    out["drop_errors"].append("lease lost: remaining staging drops not attempted")
                    break
                store.journal_intend(run_id=holder, seq=n, destination=key, kind=st.kind, target_fqn=st.target,
                                     statement_sha256=st.sha256)
                try:
                    res = writer.execute(st.sql)
                    store.journal_update(holder, n, "succeeded", statement_id=getattr(res, "statement_id", None))
                    out["dropped"].append(st.target)
                except DatabricksStatementError as exc:
                    if _statement_state(exc) != "FAILED":
                        store.journal_update(holder, n, "uncertain", statement_id=exc.statement_id, error=str(exc))
                        out["drop_errors"].append(f"{st.target}: outcome unknown ({exc})")
                        out["status"] = "needs_attention"
                        break
                    store.journal_update(holder, n, "failed", statement_id=exc.statement_id, error=str(exc))
                    out["drop_errors"].append(f"{st.target}: {exc}")
                except Exception as exc:
                    store.journal_update(holder, n, "uncertain", statement_id=getattr(exc, "statement_id", None),
                                         error=str(exc))
                    out["drop_errors"].append(f"{st.target}: outcome unknown")
                    out["status"] = "needs_attention"
                    break
            store.finish_run(holder, "succeeded" if not out["drop_errors"] else "partial", {"dropped": out["dropped"]})
    finally:
        for key in reversed(held):
            store.release_lease(key, holder)
    return out
