"""Phase 3.5: the capability-probe executor.

Runs ONLY when a user starts it (API action or scripts/run_databricks_probes.py);
never during a dry run or re-plan. Per run:

1. Preflight (read-only): resolve the warehouse, read the DBSQL version and the
   warehouse metadata. Failure -> every gate "inconclusive".
2. Build every fixture offline through the production converter -> planner ->
   writer (pipeline.py). A placement or rendering problem is a SemaBridge
   "defect", never a platform result.
3. Existence check: if any probe object name already exists, that fixture is
   not run (nothing existing is ever replaced or dropped).
4. Create the probe metric views with plain CREATE, under a guard WritePlan that
   lists exactly this run's probe objects. A run manifest records intended and
   confirmed objects.
5. Query (read-only) and compare with the expectations (compare.py).
6. Clean up in reverse order: DROP only objects whose creation was confirmed, or
   whose ownership is established afterwards (the definition carries this run's
   token). If ownership cannot be established, the object is left untouched and
   reported.
7. Record the evidence (append-only) and return the report.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from semabridge.target_backends.databricks.probes import compare, fixtures as F, pipeline
from semabridge.target_backends.databricks.probes.evidence import GATES

PROVEN, WRONG, UNSUPPORTED, INCONCLUSIVE, DEFECT, SKIPPED = (
    "proven", "wrong_result", "unsupported", "inconclusive", "defect", "skipped")
MANIFEST_ROOT = Path("output") / "databricks" / "_probes"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _classify(exc: BaseException) -> str:
    """unsupported: Databricks definitively refused the statement; inconclusive:
    no definite answer (timeout, connection, permission); defect: our own guard."""
    from semabridge.target_backends.databricks.client import DatabricksStatementError, DatabricksTimeoutError
    from semabridge.target_backends.databricks.guard import DatabricksGuardError

    if isinstance(exc, DatabricksGuardError):
        return DEFECT
    if isinstance(exc, DatabricksTimeoutError):
        return INCONCLUSIVE
    if isinstance(exc, DatabricksStatementError):
        text = str(exc).upper()
        if any(w in text for w in ("PERMISSION", "PERMISSION_DENIED", "INSUFFICIENT", "UNAUTHORIZED", "FORBIDDEN")):
            return INCONCLUSIVE
        return UNSUPPORTED
    return INCONCLUSIVE


def _dbsql_version(client) -> Tuple[Optional[str], Dict[str, Any]]:
    res = client.execute("SELECT current_version() AS v")
    raw = res.scalar()
    info: Dict[str, Any] = {}
    if isinstance(raw, dict):
        info = raw
    elif isinstance(raw, str):
        try:
            info = json.loads(raw)
        except ValueError:
            info = {"raw": raw}
    return (str(info.get("dbsql_version") or info.get("raw") or "") or None), info


class ProbeRun:
    def __init__(self, info, *, probe_catalog: str, probe_schema: str, created_by: Optional[str] = None,
                 client_factory: Optional[Callable[..., Any]] = None, manifest_root: Path = MANIFEST_ROOT,
                 fixtures: Optional[Tuple[F.Fixture, ...]] = None, record_evidence: bool = True) -> None:
        from semabridge.target_backends.databricks.client import DatabricksSqlClient

        self.info = info
        self.catalog, self.schema = probe_catalog, probe_schema
        self.created_by = created_by
        self.make_client = client_factory or (lambda guard=None, warehouse_id=None: DatabricksSqlClient(
            info, guard=guard, warehouse_id=warehouse_id, deadline_seconds=180,
            query_tags={"step": "capability_probe"}))
        self.token = uuid.uuid4().hex[:8]
        self.run_id = uuid.uuid4().hex
        self.manifest_path = manifest_root / self.run_id / "manifest.json"
        self.fixtures = fixtures or F.ALL
        self.record_evidence = record_evidence
        self.manifest: Dict[str, Any] = {"run_id": self.run_id, "token": self.token,
                                         "schema": f"{probe_catalog}.{probe_schema}", "objects": []}

    # --- manifest -------------------------------------------------------------------------

    def _save_manifest(self) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.manifest_path.with_suffix(".tmp")
        tmp.write_bytes(json.dumps(self.manifest, indent=2, default=str).encode("utf-8"))
        tmp.replace(self.manifest_path)

    def _obj(self, fqn: str) -> Dict[str, Any]:
        for o in self.manifest["objects"]:
            if o["fqn"] == fqn:
                return o
        o = {"fqn": fqn, "state": "intended"}
        self.manifest["objects"].append(o)
        return o

    # --- run -----------------------------------------------------------------------------

    def run(self) -> Dict[str, Any]:
        from semabridge.target_backends.databricks.guard import DatabricksSqlGuard, WRITE, WritePlan
        from semabridge.target_backends.databricks.warehouses import resolve_warehouse
        from semabridge.target_backends.databricks.writer import WRITER_REVISION

        started = _now()
        report: Dict[str, Any] = {"run_id": self.run_id, "token": self.token,
                                  "schema": f"{self.catalog}.{self.schema}", "fixtures": {}, "gates": {},
                                  "observations": {}, "cleanup": {"dropped": [], "left_untouched": [], "errors": []}}
        platform: Dict[str, Any] = {}
        dbsql_version: Optional[str] = None
        warehouse_id = getattr(self.info, "warehouse_id", None)
        # 1. preflight
        try:
            reader = self.make_client(warehouse_id=warehouse_id)
            if not getattr(reader, "warehouse_id", None):
                choice = resolve_warehouse(reader)
                if choice["warehouse"] is None:
                    raise RuntimeError(choice["message"])
                reader.warehouse_id = choice["warehouse"].id
                platform["warehouse"] = {"name": choice["warehouse"].name,
                                         "serverless": getattr(choice["warehouse"], "serverless", None)}
            warehouse_id = reader.warehouse_id
            dbsql_version, platform["current_version"] = _dbsql_version(reader)
        except Exception as exc:
            report["preflight_error"] = f"{type(exc).__name__}: {exc}"
            report["gates"] = {g: {"outcome": INCONCLUSIVE, "reason": "preflight failed"} for g in GATES}
            return self._finish(report, started, warehouse_id, dbsql_version, platform, WRITER_REVISION)
        report["dbsql_version"] = dbsql_version

        # 2. build every fixture offline
        built: Dict[str, Any] = {}
        for fx in self.fixtures:
            try:
                built[fx.fid] = pipeline.build(fx, catalog=self.catalog, schema=self.schema, token=self.token)
                report["fixtures"][fx.fid] = {"probe": fx.probe, "outcome": None,
                                              "skipped_measures": built[fx.fid].skipped_measures, "queries": {}}
            except Exception as exc:
                report["fixtures"][fx.fid] = {"probe": fx.probe, "outcome": DEFECT,
                                              "error": f"{type(exc).__name__}: {exc}", "queries": {}}

        # 3. existence check (read-only)
        fqns = {fid: [r.fqn for r in b.rendered] for fid, b in built.items()}
        names = [r.view for b in built.values() for r in b.rendered]
        try:
            existing = self._existing(reader, names)
        except Exception as exc:
            existing = None
            report["existence_check_error"] = f"{type(exc).__name__}: {exc}"
        for fid, b in list(built.items()):
            clash = [r.view for r in b.rendered] if existing is None else [r.view for r in b.rendered if r.view.lower() in existing]
            if clash:
                report["fixtures"][fid].update(outcome=INCONCLUSIVE,
                                               error=f"probe object name(s) already exist or could not be checked: {clash}")
                del built[fid]

        # 4-6. create, query, compare, clean up
        all_fqns = [f for fid in built for f in fqns[fid]]
        writer = self.make_client(guard=DatabricksSqlGuard(WRITE, WritePlan.for_probes(all_fqns)),
                                  warehouse_id=warehouse_id) if all_fqns else None
        created_order: List[str] = []
        try:
            for fid, b in built.items():
                self._run_fixture(b, reader, writer, report["fixtures"][fid], created_order)
        finally:
            self._cleanup(writer, reader, created_order, report["cleanup"])

        # gates
        report["gates"] = self._gates(report["fixtures"])
        report["observations"] = {fid: r for fid, r in report["fixtures"].items()
                                  if next((fx for fx in self.fixtures if fx.fid == fid), None) is not None
                                  and next(fx for fx in self.fixtures if fx.fid == fid).observation}
        return self._finish(report, started, warehouse_id, dbsql_version, platform, WRITER_REVISION)

    def _existing(self, reader, names: List[str]) -> set:
        if not names:
            return set()
        from semabridge.target_backends.databricks.writer import quote_identifier

        lit = ", ".join("'" + n.lower().replace("'", "''") + "'" for n in names)
        sch = self.schema.lower().replace("'", "''")
        rows = reader.execute(
            f"SELECT table_name FROM {quote_identifier(self.catalog)}.information_schema.tables "
            f"WHERE lower(table_schema) = '{sch}' AND lower(table_name) IN ({lit})").rows
        return {str(r.get("table_name") or "").lower() for r in rows}

    def _run_fixture(self, b, reader, writer, out: Dict[str, Any], created_order: List[str]) -> None:
        fx = b.fixture
        for r in b.rendered:
            obj = self._obj(r.fqn)
            self._save_manifest()
            try:
                writer.execute(r.create_sql)
                obj["state"] = "created"
                created_order.append(r.fqn)
            except Exception as exc:
                kind = _classify(exc)
                if kind == UNSUPPORTED:
                    obj["state"] = "refused_by_databricks"
                else:
                    obj["state"] = "creation_ambiguous"
                    obj["owned"] = self._owned(reader, r)
                    if obj["owned"]:
                        created_order.append(r.fqn)
                self._save_manifest()
                out.update(outcome=kind, error=f"CREATE {r.view}: {type(exc).__name__}: {exc}")
                return
            self._save_manifest()
        worst = PROVEN
        for q in fx.queries:
            try:
                sql, dims, ms = pipeline.query_sql(b, q, self.catalog, self.schema)
            except pipeline.FixtureDefect as exc:
                out["queries"][q.qid] = {"outcome": DEFECT, "error": str(exc)}
                worst = DEFECT
                continue
            try:
                res = reader.execute(sql)
            except Exception as exc:
                kind = _classify(exc)
                out["queries"][q.qid] = {"outcome": kind, "error": f"{type(exc).__name__}: {exc}", "sql": sql}
                worst = _worse(worst, kind)
                continue
            if fx.observation and not q.expected:
                out["queries"][q.qid] = {"outcome": "observed", "rows": [_plain(x) for x in res.rows], "sql": sql}
                continue
            result = compare.compare(q.expected, res.rows, dims, [(m, f"m_{i}") for i, m in enumerate(ms)])
            outcome = PROVEN if result["ok"] else WRONG
            out["queries"][q.qid] = {"outcome": outcome, **result, "sql": sql,
                                     "rows": [_plain(x) for x in res.rows]}
            worst = _worse(worst, outcome)
        out["outcome"] = "observed" if fx.observation else worst

    def _owned(self, reader, rendered) -> bool:
        """After an ambiguous CREATE: is the object there AND does its definition carry
        this run's token? (DESCRIBE ... EXTENDED is read-only and includes the view text.)"""
        try:
            res = reader.execute(f"DESCRIBE TABLE EXTENDED {rendered.fqn}")
            text = json.dumps([_plain(x) for x in res.rows], default=str)
            return f"probe {self.token}" in text
        except Exception:
            return False

    def _cleanup(self, writer, reader, created_order: List[str], out: Dict[str, Any]) -> None:
        for fqn in reversed(created_order):
            obj = self._obj(fqn)
            try:
                writer.execute(f"DROP VIEW IF EXISTS {fqn}")
                obj["state"] = "dropped"
                out["dropped"].append(fqn)
            except Exception as exc:
                obj["state"] = "cleanup_failed"
                out["errors"].append(f"{fqn}: {type(exc).__name__}: {exc}")
            self._save_manifest()
        for o in self.manifest["objects"]:
            if o["state"] == "creation_ambiguous" and not o.get("owned"):
                out["left_untouched"].append({"fqn": o["fqn"], "reason": "creation outcome unknown and ownership "
                                              "could not be established; left as is"})
        self._save_manifest()

    def _gates(self, results: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        def gate(fids: List[str]) -> Dict[str, Any]:
            outs = {fid: (results.get(fid) or {}).get("outcome") or INCONCLUSIVE for fid in fids}
            worst = PROVEN
            for o in outs.values():
                worst = _worse(worst, o)
            return {"outcome": worst, "fixtures": outs}

        g: Dict[str, Any] = {"baseline": gate([fx.fid for fx in F.BASELINE])}
        otm = gate([fx.fid for fx in F.CAPABILITY[F.ONE_TO_MANY]])
        g["one_to_many_joins"] = otm
        mixed = gate([fx.fid for fx in F.CAPABILITY[F.MIXED]])
        if otm["outcome"] != PROVEN and mixed["outcome"] == PROVEN:
            mixed = {**mixed, "outcome": INCONCLUSIVE, "reason": "needs one_to_many_joins proven"}
        g["mixed_root_views"] = mixed
        g["constant_coalesce"] = gate([fx.fid for fx in F.CAPABILITY[F.COALESCE_CONST]])
        return g

    def _finish(self, report, started, warehouse_id, dbsql_version, platform, writer_revision) -> Dict[str, Any]:
        from semabridge.target_backends.databricks.probes import evidence

        completed = _now()
        report.update(started_at=started.isoformat(), completed_at=completed.isoformat(),
                      warehouse_id=warehouse_id, suite_revision=F.SUITE_REVISION, writer_revision=writer_revision)
        self.manifest["report"] = {"gates": report["gates"], "cleanup": report["cleanup"]}
        try:
            self._save_manifest()
        except Exception:
            pass
        if self.record_evidence and warehouse_id:
            evidence.record(
                run_id=self.run_id, workspace_host=getattr(self.info, "host", ""), warehouse_id=warehouse_id,
                probe_schema=f"{self.catalog}.{self.schema}", dbsql_version=dbsql_version, platform=platform,
                suite_revision=F.SUITE_REVISION, writer_revision=writer_revision,
                outcomes={"gates": report["gates"], "fixtures": report["fixtures"]},
                cleanup=report["cleanup"], started_at=started,
                completed_at=completed if "preflight_error" not in report else None, created_by=self.created_by,
            )
        return report


_ORDER = {PROVEN: 0, "observed": 0, SKIPPED: 0, INCONCLUSIVE: 1, UNSUPPORTED: 2, WRONG: 3, DEFECT: 4}


def _worse(a: str, b: str) -> str:
    return a if _ORDER.get(a, 1) >= _ORDER.get(b, 1) else b


def _plain(row: Dict[str, Any]) -> Dict[str, Any]:
    from decimal import Decimal

    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in row.items()}


def run_probes(info, *, probe_catalog: str, probe_schema: str, created_by: Optional[str] = None,
               **kwargs) -> Dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_]+", probe_catalog or "") or not re.fullmatch(r"[A-Za-z0-9_]+", probe_schema or ""):
        raise ValueError("probe catalog and schema must be plain identifiers of an existing schema")
    return ProbeRun(info, probe_catalog=probe_catalog, probe_schema=probe_schema, created_by=created_by,
                    **kwargs).run()
