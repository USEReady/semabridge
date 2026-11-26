"""Phase 3.8a: decide what a deploy would do -- read-only.

``prepare`` takes the approved bundle and the live state and returns a
DeployPreview: the exact statements in order, the guard WritePlan for exactly
those, and every object that is blocked or unchanged, with reasons. It never
writes to Databricks and never rewrites the bundle; when the world changed
since the plan, affected views are blocked and the user re-plans.

Order of checks (design v2, section 4): bundle -> live basics (version,
identity, metastore) -> lease and unreconciled journal entries -> model-wide
blockers -> capability proof -> destination permissions -> source binding
revalidation -> stub tables -> views (ownership, owner, drift) -> breaking
changes and unused objects.
"""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from semabridge.target_backends.databricks.deploy import definitions as D
from semabridge.target_backends.databricks.deploy import statements as S
from semabridge.target_backends.databricks.deploy.live import Live

PROVENANCE = "Created by SemaBridge"
REQUIRED_DEST_PRIVILEGES = ("USE_CATALOG", "USE_SCHEMA", "CREATE_TABLE")
STUB_RESOLVABLE = "source_table_missing"
VERIFIED, INCOMPLETE = "verified_structure", "incomplete_unverified"


@dataclass
class DeployRequest:
    project_id: str
    model_key: str
    out_dir: Path
    plan: Dict[str, Any]                 # the recorded plan (its meta.revision is deployed)
    workspace_host: Optional[str]
    warehouse_id: Optional[str]
    allow_list: Iterable[str] = ()
    create_stubs: bool = False           # the user authorises creating the empty stub tables
    stub_form: str = "empty_table"       # the project's CURRENT setting (must equal the bundle's)
    lease_run_id: Optional[str] = None   # set when prepare runs inside a deploy (lease held)
    run_id: Optional[str] = None


@dataclass
class Item:
    kind: str                            # view | stub_table | source_table
    name: str                            # view name / model dataset
    fqn: Optional[str] = None
    action: str = "pending"              # create | update | unchanged | blocked | exists_owned | ok
    reasons: List[Dict[str, Any]] = field(default_factory=list)
    readiness: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def block(self, code: str, message: str, **details: Any) -> None:
        self.action = "blocked"
        self.reasons.append({"code": code, "message": message, **({"details": details} if details else {})})

    def as_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "name": self.name, "fqn": self.fqn, "action": self.action,
                "reasons": list(self.reasons), "readiness": self.readiness, "details": dict(self.details)}


@dataclass
class DeployPreview:
    run_id: str
    token: str
    plan_revision: Optional[int] = None
    destination: Optional[Dict[str, str]] = None
    lease_key: Optional[str] = None
    platform: Dict[str, Any] = field(default_factory=dict)
    model_blockers: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    views: List[Item] = field(default_factory=list)
    stub_tables: List[Item] = field(default_factory=list)
    sources: List[Item] = field(default_factory=list)
    statements: List[S.Statement] = field(default_factory=list)
    checks: List[Dict[str, Any]] = field(default_factory=list)      # read-only validations 3.8b runs
    breaking_changes: List[Dict[str, Any]] = field(default_factory=list)
    unused: List[Dict[str, Any]] = field(default_factory=list)
    missing_grants: List[str] = field(default_factory=list)
    names_to_record: List[Dict[str, Any]] = field(default_factory=list)
    preview_only: bool = True

    def block_model(self, code: str, message: str, **details: Any) -> None:
        self.model_blockers.append({"code": code, "message": message, **({"details": details} if details else {})})

    @property
    def status(self) -> str:
        if self.model_blockers:
            return "blocked"
        if self.statements:
            return "ready"
        if any(v.action == "blocked" for v in self.views):
            return "blocked"
        return "no_op"

    def write_plan(self):
        return S.write_plan(self.statements)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id, "status": self.status, "plan_revision": self.plan_revision,
            "destination": self.destination, "lease_key": self.lease_key, "platform": self.platform,
            "preview_only": self.preview_only, "model_blockers": list(self.model_blockers),
            "warnings": list(self.warnings), "views": [v.as_dict() for v in self.views],
            "stub_tables": [t.as_dict() for t in self.stub_tables], "sources": [s.as_dict() for s in self.sources],
            "statements": [st.as_dict() for st in self.statements], "checks": list(self.checks),
            "breaking_changes": list(self.breaking_changes), "unused": list(self.unused),
            "missing_grants": list(self.missing_grants),
        }


def _norm_type(t: Optional[str]) -> str:
    return "".join(str(t or "").lower().split())


def _cols(columns: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    return {str(c.get("name") or "").lower(): _norm_type(c.get("type")) for c in columns if c.get("name")}


def _dataset_views(manifest: Dict[str, Any]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for v in manifest.get("views") or []:
        for ds in v.get("tables") or []:
            out.setdefault(str(ds), []).append(v["view"])
    return out


def prepare(req: DeployRequest, live: Live, *, store: Any = None, now: Optional[datetime] = None,
            token: Optional[str] = None) -> DeployPreview:
    from semabridge.target_backends.databricks import bundle
    from semabridge.target_backends.databricks.guard import normalize_name

    if store is None:
        from semabridge.target_backends.databricks.deploy import store as store_mod

        store = store_mod
    pv = DeployPreview(run_id=req.run_id or uuid.uuid4().hex, token=token or secrets.token_hex(4))
    plan = req.plan or {}
    meta = plan.get("meta") or {}
    rev = meta.get("revision")
    pv.plan_revision = int(rev) if rev is not None else None

    # 1. bundle ---------------------------------------------------------------------------------
    if meta.get("status") == "stale":
        pv.block_model("plan_stale", "the plan is out of date: re-plan or run the dry run again")
        return pv
    if pv.plan_revision is None:
        pv.block_model("plan_revision_missing", "the plan has no revision: run the dry run again")
        return pv
    try:
        manifest = bundle.deployable_bundle(Path(req.out_dir), pv.plan_revision)
    except bundle.BundleError as exc:
        pv.block_model("bundle_not_deployable", str(exc))
        return pv
    dest = manifest.get("destination") or {}
    catalog, schema = str(dest.get("catalog") or ""), str(dest.get("schema") or "")
    if not catalog or not schema or catalog == "None" or schema == "None":
        pv.block_model("output_location_missing", "the bundle has no output catalog/schema")
        return pv
    pv.destination = {"catalog": catalog, "schema": schema}
    folder = Path(req.out_dir) / bundle.VIEWS_DIR / str(pv.plan_revision)
    bodies = {v["view"]: (folder / v["file"]).read_bytes().decode("utf-8") for v in manifest.get("views") or []}
    stub_form = str(manifest.get("stub_form") or "empty_table")
    if stub_form != str(req.stub_form or "empty_table"):
        pv.block_model("stub_form_changed",
                       f"the plan was made with stub form '{stub_form}' but the project now says "
                       f"'{req.stub_form}': re-plan (deploy never switches stub form after preview)")

    # 2. live basics ------------------------------------------------------------------------------
    try:
        version, identity, metastore = live.version(), live.identity(), live.metastore()
    except Exception as exc:
        pv.block_model("databricks_unreachable", f"Databricks could not be read: {type(exc).__name__}: {exc}")
        return pv
    pv.platform = {"dbsql_version": version, "identity": identity, "metastore": metastore,
                   "workspace_host": req.workspace_host, "warehouse_id": req.warehouse_id,
                   "warehouse_can_use": True}   # the reads above ran on this warehouse
    if not identity:
        pv.block_model("identity_unknown", "the deploying identity could not be read")

    # 3. lease and journal ---------------------------------------------------------------------------
    pv.lease_key = store.lease_key(metastore, catalog, schema)
    if req.lease_run_id:
        pv.preview_only = False
        if not store.holds_lease(pv.lease_key, req.lease_run_id, now=now):
            pv.block_model("lease_not_held", "this run does not hold the deploy lease for the schema")
    else:
        pv.warnings.append("preview: checks are repeated under the deploy lease before anything is written")
    open_ops = store.open_operations(pv.lease_key)
    if open_ops:
        pv.block_model("reconcile_required",
                       f"{len(open_ops)} earlier deploy operation(s) in this schema have an unknown outcome: "
                       "reconcile before deploying", operations=[{k: o[k] for k in ("run_id", "seq", "kind", "target_fqn")}
                                                                 for o in open_ops])

    # 4. model-wide blockers; table / view blockers --------------------------------------------------
    blocked_views: Dict[str, List[Dict[str, Any]]] = {}
    resolvable: Dict[str, Dict[str, Any]] = {}
    tables = {str(t["dataset"]): t for t in manifest.get("tables") or []}
    for b in manifest.get("blockers") or []:
        scope = b.get("scope") or "model"
        reason = {"code": b.get("code"), "message": b.get("message")}
        if scope == "model":
            pv.block_model(str(b.get("code")), str(b.get("message")))
        elif scope == "view":
            for v in ([b.get("view")] if b.get("view") else []) + list(b.get("views") or []):
                blocked_views.setdefault(str(v), []).append(reason)
        else:
            ds = str(b.get("table") or "")
            t = tables.get(ds) or {}
            if (b.get("code") == STUB_RESOLVABLE and req.create_stubs and stub_form == "empty_table"
                    and t.get("name") and t.get("columns")):
                resolvable[ds] = t
                continue
            why = dict(reason)
            if b.get("code") == STUB_RESOLVABLE:
                why["message"] += (" (stub creation not authorised for this deploy)" if not req.create_stubs else
                                   " (inline stubs are not deployable yet)" if stub_form != "empty_table" else
                                   " (no approved column definition)")
            for v in b.get("views") or []:
                blocked_views.setdefault(str(v), []).append(why)

    # 5. capability proof -----------------------------------------------------------------------------
    _check_proof(pv, req, manifest, version, now)

    # 6. destination permissions -------------------------------------------------------------------------
    if identity:
        privs = live.schema_privileges(catalog, schema, identity)
        if privs is None:
            pv.warnings.append("destination privileges could not be read; Databricks will refuse the deploy "
                               "if USE CATALOG / USE SCHEMA / CREATE TABLE are missing")
        elif not ({"ALL_PRIVILEGES", "OWNER"} & privs):
            missing = [p for p in REQUIRED_DEST_PRIVILEGES if p not in privs]
            if missing:
                pv.missing_grants = _grant_texts(missing, catalog, schema, identity)
                pv.block_model("destination_permissions_missing",
                               f"{identity} lacks {', '.join(missing)} on {catalog}.{schema} (metric views need "
                               "CREATE TABLE on the schema)", missing=missing)

    # 7. source bindings -------------------------------------------------------------------------------------
    deps = _dataset_views(manifest)
    _check_sources(pv, live, tables, deps, blocked_views)

    # 8. destination objects, ownership ------------------------------------------------------------------------
    try:
        existing = live.objects(catalog, schema)
    except Exception as exc:
        pv.block_model("destination_unreadable", f"objects in {catalog}.{schema} could not be listed: {exc}")
        existing = {}
    owned = store.owned_objects(req.project_id, req.model_key)

    stub_statements: List[S.Statement] = []
    created_stubs: List[str] = []
    for ds in sorted(resolvable):
        t = resolvable[ds]
        item = Item("stub_table", ds, readiness=INCOMPLETE)
        fqn = normalize_name(f"`{catalog}`.`{schema}`.`{t['name']}`")
        item.fqn = fqn
        item.details["columns"] = list(t["columns"])
        if (t.get("catalog"), t.get("schema")) != (catalog, schema):
            item.block("stub_location_mismatch", "the proposed table is not in the deploy destination")
        elif str(t["name"]).lower() in existing:
            ours, why = _owned_stub(live, owned.get(fqn), fqn)
            if not ours:
                item.block("name_taken", f"{fqn} already exists and is not a SemaBridge stub of this model ({why})")
            else:
                info = live.table_info(fqn) or {}
                if _cols(info.get("columns") or []) != _cols(t["columns"]):
                    item.block("stub_schema_changed",
                               f"{fqn} exists (ours) but its columns or types differ from the approved definition; "
                               "it is never altered")
                else:
                    item.action = "exists_owned"
        else:
            try:
                st = S.stub_table(catalog, schema, t["name"], [(c["name"], c["type"]) for c in t["columns"]],
                                  {"semabridge.project": _prop(req.project_id), "semabridge.run": pv.run_id,
                                   "semabridge.model_key": _prop(req.model_key)})
                stub_statements.append(st)
                created_stubs.append(ds)
                item.action = "create"
            except S.StatementError as exc:
                item.block("stub_definition_invalid", str(exc))
        if item.action == "blocked":
            for v in t.get("views") or []:
                blocked_views.setdefault(str(v), []).append(
                    {"code": "stub_table_blocked", "message": f"needs table {ds}: {item.reasons[-1]['message']}"})
        pv.stub_tables.append(item)

    # 9. views ---------------------------------------------------------------------------------------------------
    view_statements: List[S.Statement] = []
    bundle_views = [v["view"] for v in manifest.get("views") or []]
    for mv in manifest.get("views") or []:
        name, body = mv["view"], bodies[mv["view"]]
        fqn = normalize_name(f"`{catalog}`.`{schema}`.`{name}`")
        nonbound = [ds for ds in mv.get("tables") or [] if (tables.get(ds) or {}).get("status") != "bound"]
        item = Item("view", name, fqn=fqn, readiness=INCOMPLETE if nonbound else VERIFIED)
        if nonbound:
            item.details["stub_backed"] = nonbound
        for r in blocked_views.get(name, []):
            item.block(r["code"], r["message"])
        new_hash = D.definition_hash(body)
        item.details["definition_hash"] = new_hash
        staging = S.staging_name(name, pv.token)
        if staging.lower() in existing:
            item.block("staging_name_taken", f"staging name {staging} already exists")
        if item.action != "blocked":
            if name.lower() not in existing:
                item.action = "create"
            else:
                _existing_view(item, live, owned.get(fqn), identity, new_hash)
        if item.action in ("create", "update"):
            measures = D.names(body)["measures"]
            seq = [S.create_view(catalog, schema, name, body, mv["body_sha256"], staging_token=pv.token)]
            seq.append(S.create_view(catalog, schema, name, body, mv["body_sha256"]) if item.action == "create"
                       else S.alter_view(catalog, schema, name, body, mv["body_sha256"]))
            seq.append(S.drop_staging(catalog, schema, name, pv.token))
            view_statements += seq
            pv.checks.append(_staging_checks(name, seq[0].target, measures))
        pv.views.append(item)

    if not pv.model_blockers:
        pv.statements = stub_statements + view_statements
    _report_history(pv, req, store, manifest, bodies, owned, existing)
    deployable = {v.name for v in pv.views if v.action in ("create", "update", "unchanged")}
    from semabridge.target_backends.databricks.deploy.names import names_from_bundle_views

    pv.names_to_record = names_from_bundle_views(plan.get("views") or [], deployable, manifest.get("tables") or [],
                                                 created_stubs + [i.name for i in pv.stub_tables
                                                                  if i.action == "exists_owned"])
    return pv


def _staging_checks(view: str, staging_fqn: str, measures: List[str]) -> Dict[str, Any]:
    """Read-only validation of a staging view (3.8b runs these through the guard).
    Structural / query-analysis only: LIMIT 0 never verifies numbers."""
    from semabridge.target_backends.databricks.deploy.live import _quoted
    from semabridge.target_backends.databricks.writer import quote_identifier

    q = _quoted(staging_fqn)
    sql = [f"DESCRIBE TABLE EXTENDED {q}"]
    if measures:
        sql.append(f"SELECT {', '.join(f'MEASURE({quote_identifier(m)})' for m in measures)} FROM {q} LIMIT 0")
    return {"view": view, "staging": staging_fqn, "sql": sql, "structural_only": True}


def _prop(value: Any) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9_.:\-]", "_", str(value or ""))[:128]


def _grant_texts(missing: List[str], catalog: str, schema: str, principal: str) -> List[str]:
    from semabridge.target_backends.databricks.writer import quote_identifier

    who = quote_identifier(principal)
    out = []
    for p in missing:
        if p == "USE_CATALOG":
            out.append(f"GRANT USE CATALOG ON CATALOG {quote_identifier(catalog)} TO {who};")
        elif p == "USE_SCHEMA":
            out.append(f"GRANT USE SCHEMA ON SCHEMA {quote_identifier(catalog)}.{quote_identifier(schema)} TO {who};")
        elif p == "CREATE_TABLE":
            out.append(f"GRANT CREATE TABLE ON SCHEMA {quote_identifier(catalog)}.{quote_identifier(schema)} TO {who};")
    return out


def _check_proof(pv: DeployPreview, req: DeployRequest, manifest: Dict[str, Any], version: Optional[str],
                 now: Optional[datetime]) -> None:
    from semabridge.target_backends.databricks.probes.evidence import effective_capabilities

    effective, ref = effective_capabilities(req.workspace_host, req.warehouse_id, req.allow_list, now=now)
    pv.platform["proof"] = {k: ref.get(k) for k in ("status", "reason", "run_id", "dbsql_version", "enabled")}
    if ref.get("status") != "valid":
        pv.block_model("proof_not_valid", f"no valid capability proof (baseline included): {ref.get('reason')}. "
                       "Run the probes (they never run automatically)")
        return
    if str(ref.get("dbsql_version") or "") != str(version or ""):
        pv.block_model("proof_version_mismatch",
                       f"the probes ran on DBSQL {ref.get('dbsql_version')} but the warehouse is now on {version}: "
                       "run the probes again")
    needed = set(manifest.get("capabilities") or [])
    missing = sorted(needed - set(effective))
    if missing:
        pv.block_model("capability_not_effective",
                       f"the plan uses {missing}, which are not proven-and-allowed now: re-plan (capabilities are "
                       "never silently downgraded)", capabilities=missing)


def _check_sources(pv: DeployPreview, live: Live, tables: Dict[str, Dict[str, Any]], deps: Dict[str, List[str]],
                   blocked_views: Dict[str, List[Dict[str, Any]]]) -> None:
    bound = {ds: t for ds, t in tables.items() if t.get("status") == "bound" and ds in deps}
    if not bound:
        return
    try:
        meta = live.source_tables({(t["catalog"], t["schema"]) for t in bound.values()})
    except Exception as exc:
        pv.block_model("sources_unreadable", f"source tables could not be re-checked: {exc}")
        return
    for ds in sorted(bound):
        t = bound[ds]
        fqn = f"{t['catalog']}.{t['schema']}.{t['name']}"
        item = Item("source_table", ds, fqn=fqn, action="ok")
        uc = (meta.get((t["catalog"], t["schema"])) or {}).get(str(t["name"]).upper())
        if uc is None:
            item.block("source_missing", f"{fqn} no longer exists: re-plan required")
        else:
            live_cols = {k.lower(): _norm_type(v[1]) for k, v in uc.columns.items()}
            changed = [c["name"] for c in t.get("columns") or []
                       if live_cols.get(str(c["name"]).lower()) != _norm_type(c.get("type"))]
            if changed:
                item.block("source_changed", f"{fqn}: columns missing or retyped since the plan ({', '.join(changed)}): "
                           "re-plan required", columns=changed)
            ok, err = live.can_select(fqn)
            if not ok:
                item.block("source_no_access", f"no SELECT access to {fqn}: {err}")
            try:
                info = live.table_info(fqn) or {}
                item.details["uc_object_id"] = info.get("id")
            except Exception:
                item.details["uc_object_id"] = None
        if item.action == "blocked":
            for v in deps.get(ds, []):
                blocked_views.setdefault(v, []).append({"code": item.reasons[-1]["code"],
                                                        "message": item.reasons[-1]["message"]})
        pv.sources.append(item)


def _owned_stub(live: Live, rec: Optional[Dict[str, Any]], fqn: str) -> Tuple[bool, str]:
    if not rec or rec.get("kind") != "stub_table":
        return False, "no deploy record"
    info = live.table_info(fqn)
    if info is None:
        return False, "not readable"
    if rec.get("uc_object_id") and info.get("id") and rec["uc_object_id"] != info["id"]:
        return False, "a different object now has this name"
    if str((info.get("properties") or {}).get("semabridge.stub")).lower() != "true":
        return False, "the stub marker property is missing"
    return True, ""


def _existing_view(item: Item, live: Live, rec: Optional[Dict[str, Any]], identity: Optional[str],
                   new_hash: str) -> None:
    fqn = item.fqn or ""
    if not rec or rec.get("kind") != "view":
        item.block("name_taken", f"{fqn} exists and SemaBridge has no deploy record of creating it; it is never replaced")
        return
    info = live.table_info(fqn)
    if info is None:
        item.block("view_unreadable", f"{fqn} exists but could not be read")
        return
    if rec.get("uc_object_id") and info.get("id"):
        if rec["uc_object_id"] != info["id"]:
            item.block("identity_changed", f"{fqn} is a different object than the one SemaBridge deployed")
            return
    elif PROVENANCE not in str(info.get("comment") or "") and PROVENANCE not in str(info.get("view_definition") or ""):
        item.block("ownership_unconfirmed", f"{fqn}: no object id to compare and no SemaBridge provenance")
        return
    if identity and info.get("owner") and str(info["owner"]).lower() != str(identity).lower():
        item.block("not_owner", f"{fqn} is owned by {info['owner']}; updating a view's definition requires its owner")
        return
    body = D.extract_yaml(info.get("view_definition")) or D.extract_yaml(live.show_create(fqn))
    if body is None:
        item.block("definition_unreadable", f"the live definition of {fqn} could not be read back")
        return
    live_hash = D.definition_hash(body)
    item.details["live_definition_hash"] = live_hash
    if rec.get("definition_hash") and live_hash != rec["definition_hash"]:
        item.block("manual_drift", f"{fqn} was changed outside SemaBridge since the last deploy; it is not overwritten")
        return
    item.action = "unchanged" if live_hash == new_hash else "update"
    item.details["previous_hash"] = live_hash


def _report_history(pv: DeployPreview, req: DeployRequest, store: Any, manifest: Dict[str, Any],
                    bodies: Dict[str, str], owned: Dict[str, Dict[str, Any]], existing: Dict[str, str]) -> None:
    """Breaking interface changes (vs the last deployed names) and unused objects."""
    try:
        history = store.deployed_names(req.project_id, req.model_key)
    except Exception:
        history = []
    new_views = {v["view"] for v in manifest.get("views") or []}
    by_view: Dict[str, Dict[str, Set[str]]] = {}
    for n, body in bodies.items():
        d = D.names(body)
        by_view[n] = {"field": set(d["fields"]), "measure": set(d["measures"])}
    old_view_by_source = {r["view_source"]: r["target_name"] for r in history if r["kind"] == "view"}
    new_view_by_source: Dict[str, str] = {}
    for v in req.plan.get("views") or []:
        new_view_by_source[str(v.get("source_dataset"))] = str(v.get("name"))
    for src, old in sorted(old_view_by_source.items()):
        new = new_view_by_source.get(src)
        if new is None:
            pv.breaking_changes.append({"kind": "view_removed", "view": old})
        elif new != old:
            pv.breaking_changes.append({"kind": "view_renamed", "from": old, "to": new})
    seen: Dict[Tuple[str, str, str], str] = {}
    for r in history:
        if r["kind"] in ("field", "measure"):
            seen[(r["view_source"], r["kind"], json.dumps(r["source_key"], sort_keys=True))] = r["target_name"]
    new_keys: Dict[Tuple[str, str, str], Tuple[str, str]] = {}
    for v in req.plan.get("views") or []:
        src = str(v.get("source_dataset"))
        for kind, items in (("field", v.get("fields") or []), ("measure", v.get("measures") or [])):
            for it in items:
                from semabridge.target_backends.databricks.deploy.names import source_key

                new_keys[(src, kind, json.dumps(source_key(it.get("source_id")), sort_keys=True))] = (str(v["name"]), str(it["name"]))
    for key, old_name in sorted(seen.items()):
        src, kind, _ = key
        view = old_view_by_source.get(src)
        if key not in new_keys:
            pv.breaking_changes.append({"kind": f"{kind}_removed", "view": view, "name": old_name})
        elif new_keys[key][1] != old_name:
            pv.breaking_changes.append({"kind": f"{kind}_renamed", "view": new_keys[key][0], "from": old_name,
                                        "to": new_keys[key][1]})
    dest = pv.destination or {}
    prefix = f"{str(dest.get('catalog')).lower()}.{str(dest.get('schema')).lower()}."
    needed_stubs = {i.fqn for i in pv.stub_tables}
    for fqn, rec in sorted(owned.items()):
        if not fqn.startswith(prefix) or fqn[len(prefix):] not in existing:
            continue
        name = fqn[len(prefix):]
        if rec["kind"] == "view" and name not in {n.lower() for n in new_views}:
            pv.unused.append({"kind": "view", "fqn": fqn, "note": "no longer in the plan; reported, never dropped"})
        elif rec["kind"] == "stub_table" and fqn not in needed_stubs:
            pv.unused.append({"kind": "stub_table", "fqn": fqn, "note": "no view needs it now; reported, never dropped"})
