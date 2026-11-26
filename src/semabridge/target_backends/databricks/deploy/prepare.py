"""Phase 3.8a: decide what a deploy would do -- read-only.

``prepare`` takes the approved bundle and the live state and returns a
DeployPreview: the exact statements in order, the guard WritePlan for exactly
those, and every object that is blocked, unchanged or verified, with reasons.
It never writes to Databricks and never rewrites the bundle; when the world
changed since the plan, affected views are blocked and the user re-plans.

Order of checks (design v2 + review corrections): bundle -> live basics ->
lease and unreconciled journal entries (destination AND staging schema) ->
deploy history -> model-wide blockers -> capability proof -> destination
permissions -> restricted staging schema -> source binding revalidation
(plan-time UC identity) -> stub tables -> views (UC identity ownership,
owner, drift, incomplete opt-in) -> destination access for incomplete views ->
breaking changes and unused objects.

Ownership is established ONLY by a verified deploy record whose Unity Catalog
object id equals the live object's id. Provenance comments and properties are
supporting evidence, never enough for an automatic change.
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
# Catalog-level privileges that do NOT expose a schema's objects to their holder.
NON_EXPOSING_CATALOG_PRIVILEGES = {"USE_CATALOG", "BROWSE"}
STUB_RESOLVABLE = "source_table_missing"
VERIFIED, INCOMPLETE = "verified_structure", "incomplete_unverified"
from semabridge.target_backends.databricks.writer import INCOMPLETE_WARNING  # noqa: E402  (one source of truth)


@dataclass
class DeployRequest:
    project_id: str
    model_key: str
    out_dir: Path
    plan: Dict[str, Any]                 # the recorded plan (its meta.revision is deployed)
    workspace_host: Optional[str]
    warehouse_id: Optional[str]
    allow_list: Iterable[str] = ()
    create_stubs: bool = False           # user choice per deploy: create the empty stub tables (off by default)
    publish_incomplete: bool = False     # user choice per deploy: publish stub-backed views (off by default)
    stub_form: str = "empty_table"       # the project's CURRENT setting (must equal the bundle's)
    staging: Optional[Tuple[str, str]] = None   # the project's restricted staging (catalog, schema)
    deploy_principals: Iterable[str] = ()       # principals allowed access to the staging schema
    lease_run_id: Optional[str] = None   # set when prepare runs inside a deploy (leases held)
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
    staging: Optional[Dict[str, str]] = None
    lease_key: Optional[str] = None
    staging_lease_key: Optional[str] = None
    platform: Dict[str, Any] = field(default_factory=dict)
    model_blockers: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    views: List[Item] = field(default_factory=list)
    stub_tables: List[Item] = field(default_factory=list)
    sources: List[Item] = field(default_factory=list)
    statements: List[S.Statement] = field(default_factory=list)
    checks: List[Dict[str, Any]] = field(default_factory=list)      # read-only validations 3.8b runs
    verified_objects: List[Dict[str, Any]] = field(default_factory=list)   # unchanged: re-recorded as verified
    breaking_changes: List[Dict[str, Any]] = field(default_factory=list)
    unused: List[Dict[str, Any]] = field(default_factory=list)
    missing_grants: List[str] = field(default_factory=list)
    destination_access: Optional[List[Dict[str, Any]]] = None       # shown when incomplete views are published
    names_to_record: List[Dict[str, Any]] = field(default_factory=list)
    bundle_hashes: Dict[str, str] = field(default_factory=dict)     # view -> body sha256 (bound into the intent)
    stub_form: Optional[str] = None
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
            "destination": self.destination, "staging": self.staging, "lease_key": self.lease_key,
            "staging_lease_key": self.staging_lease_key, "platform": self.platform,
            "preview_only": self.preview_only, "model_blockers": list(self.model_blockers),
            "warnings": list(self.warnings), "views": [v.as_dict() for v in self.views],
            "stub_tables": [t.as_dict() for t in self.stub_tables], "sources": [s.as_dict() for s in self.sources],
            "statements": [st.as_dict() for st in self.statements], "checks": list(self.checks),
            "verified_objects": list(self.verified_objects),
            "breaking_changes": list(self.breaking_changes), "unused": list(self.unused),
            "missing_grants": list(self.missing_grants), "destination_access": self.destination_access,
            "bundle_hashes": dict(self.bundle_hashes), "names_to_record": list(self.names_to_record),
            "stub_form": self.stub_form,
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
    pv.bundle_hashes = {v["view"]: v["body_sha256"] for v in manifest.get("views") or []}
    stub_form = str(manifest.get("stub_form") or "empty_table")
    pv.stub_form = stub_form
    if stub_form != str(req.stub_form or "empty_table"):
        pv.block_model("stub_form_changed",
                       f"the plan was made with stub form '{stub_form}' but the project now says "
                       f"'{req.stub_form}': re-plan (deploy never switches stub form after preview)")
    if meta.get("names_history") == "unreadable":
        pv.block_model("plan_without_name_history",
                       "this plan was made while the deploy history could not be read, so deployed names may "
                       "not be preserved: re-plan once the history is readable")

    # 2. staging location (restricted schema, configured per project) -----------------------------
    stg_catalog, stg_schema = (req.staging or (None, None))
    if not stg_catalog or not stg_schema:
        pv.block_model("staging_schema_missing", "no restricted staging schema is configured for this project")
    elif (stg_catalog.lower(), stg_schema.lower()) == (catalog.lower(), schema.lower()):
        pv.block_model("staging_schema_is_destination", "the staging schema must not be the destination schema")
    else:
        pv.staging = {"catalog": stg_catalog, "schema": stg_schema}

    # 3. live basics ------------------------------------------------------------------------------
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

    # 4. leases and journal (destination and staging schema) -----------------------------------------
    pv.lease_key = store.lease_key(metastore, catalog, schema)
    keys = [pv.lease_key]
    if pv.staging:
        pv.staging_lease_key = store.lease_key(metastore, stg_catalog, stg_schema)
        keys.append(pv.staging_lease_key)
    if req.lease_run_id:
        pv.preview_only = False
        for key in keys:
            if not store.holds_lease(key, req.lease_run_id, now=now):
                pv.block_model("lease_not_held", f"this run does not hold the deploy lease {key}")
    else:
        pv.warnings.append("preview: checks are repeated under the deploy lease before anything is written")
    try:
        open_ops = [o for key in keys for o in store.open_operations(key)]
    except Exception as exc:
        pv.block_model("journal_unavailable", f"the deploy journal cannot be read: {exc}")
        open_ops = []
    if open_ops:
        pv.block_model("reconcile_required",
                       f"{len(open_ops)} earlier deploy operation(s) here have an unknown outcome: reconcile first",
                       operations=[{k: o[k] for k in ("run_id", "seq", "kind", "target_fqn")} for o in open_ops])

    # 5. deploy history (required) -----------------------------------------------------------------
    try:
        owned = store.owned_objects(req.project_id, req.model_key)
        history = store.deployed_names(req.project_id, req.model_key)
    except Exception as exc:
        pv.block_model("deploy_history_unreadable", f"the deploy history cannot be read: {exc}")
        owned, history = {}, []
    ref = meta.get("cache_ref") or {}
    plan_project = str(ref.get("binding_project_id") or ref.get("project_id") or "")
    if plan_project and plan_project != str(req.project_id) and (owned or history):
        pv.block_model("plan_from_other_project",
                       f"this plan was made under '{plan_project}' (e.g. the wizard), which does not know the names "
                       "already deployed for this project: run the dry run in this project")

    # 6. model-wide / view / table blockers ------------------------------------------------------------
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
                why["message"] += (" (empty-table creation was not chosen for this deploy)" if not req.create_stubs else
                                   " (inline stubs are not deployable yet)" if stub_form != "empty_table" else
                                   " (no approved column definition)")
            for v in b.get("views") or []:
                blocked_views.setdefault(str(v), []).append(why)

    # 7. capability proof -------------------------------------------------------------------------------
    _check_proof(pv, req, manifest, version, now)

    # 8. destination permissions ---------------------------------------------------------------------------
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

    # 9. restricted staging schema: access limited to authorised deploy/admin identities ------------------
    if pv.staging and identity:
        _check_staging_isolation(pv, live, stg_catalog, stg_schema, identity, req.deploy_principals)

    # 10. source bindings ------------------------------------------------------------------------------------
    deps = _dataset_views(manifest)
    _check_sources(pv, live, tables, deps, blocked_views)

    # 11. destination objects ------------------------------------------------------------------------------
    try:
        existing = live.objects(catalog, schema)
        staged = live.objects(stg_catalog, stg_schema) if pv.staging else {}
    except Exception as exc:
        pv.block_model("destination_unreadable", f"objects in {catalog}.{schema} could not be listed: {exc}")
        existing, staged = {}, {}

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
            _existing_stub(item, live, owned.get(fqn), t)
            if item.action == "exists_owned":
                pv.verified_objects.append({"kind": "stub_table", "fqn": fqn, "source": ds,
                                            "uc_object_id": item.details.get("uc_object_id")})
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

    # 12. views -------------------------------------------------------------------------------------------------
    view_statements: List[S.Statement] = []
    for mv in manifest.get("views") or []:
        name, body = mv["view"], bodies[mv["view"]]
        fqn = normalize_name(f"`{catalog}`.`{schema}`.`{name}`")
        nonbound = [ds for ds in mv.get("tables") or [] if (tables.get(ds) or {}).get("status") != "bound"]
        item = Item("view", name, fqn=fqn, readiness=INCOMPLETE if nonbound else VERIFIED)
        if nonbound:
            item.details["stub_backed"] = nonbound
            if INCOMPLETE_WARNING not in body:
                item.block("incomplete_warning_missing",
                           "a stub-backed view's approved definition lacks the INCOMPLETE warning: re-plan")
            if not req.publish_incomplete:
                item.block("incomplete_not_selected",
                           "this view reads empty placeholder tables; publishing incomplete views was not chosen")
        for r in blocked_views.get(name, []):
            item.block(r["code"], r["message"])
        new_hash = D.definition_hash(body)
        item.details["definition_hash"] = new_hash
        if pv.staging:
            staging = S.staging_name(name, pv.token)
            if staging.lower() in staged:
                item.block("staging_name_taken", f"staging name {staging} already exists")
        if item.action != "blocked":
            if name.lower() not in existing:
                item.action = "create"
            else:
                _existing_view(item, live, owned.get(fqn), identity, new_hash)
                if item.action == "unchanged":
                    pv.verified_objects.append({"kind": "view", "fqn": fqn, "source": name,
                                                "uc_object_id": item.details.get("uc_object_id"),
                                                "definition_hash": new_hash})
        if item.action in ("create", "update") and pv.staging:
            measures = D.names(body)["measures"]
            seq = [S.create_staging(stg_catalog, stg_schema, name, body, mv["body_sha256"], pv.token)]
            seq.append(S.create_view(catalog, schema, name, body, mv["body_sha256"]) if item.action == "create"
                       else S.alter_view(catalog, schema, name, body, mv["body_sha256"]))
            seq.append(S.drop_staging(stg_catalog, stg_schema, name, pv.token))
            view_statements += seq
            pv.checks.append(_staging_checks(name, seq[0].target, measures))
        pv.views.append(item)

    # 13. destination access when incomplete definitions are published -------------------------------------
    if any(v.action in ("create", "update") and v.readiness == INCOMPLETE for v in pv.views):
        access = _schema_access(live, catalog, schema)
        if access is None:
            pv.block_model("destination_access_unreadable",
                           "incomplete views would be published but who can read the destination cannot be "
                           "established")
        else:
            pv.destination_access = access
            pv.warnings.append("incomplete views will be readable by every principal listed in destination_access")

    if not pv.model_blockers:
        pv.statements = stub_statements + view_statements
    _report_history(pv, req, history, manifest, bodies, owned, existing)
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


def _schema_access(live: Live, catalog: str, schema: str) -> Optional[List[Dict[str, Any]]]:
    """Everyone with access to a schema's objects: direct schema grants, exposing
    catalog grants (inherited) and both owners. None when any part is unreadable."""
    sch = live.grants("schema", f"{catalog}.{schema}")
    cat = live.grants("catalog", catalog)
    s_owner, c_owner = live.owner("schema", f"{catalog}.{schema}"), live.owner("catalog", catalog)
    if sch is None or cat is None or not s_owner or not c_owner:
        return None
    out = [{"principal": g["principal"], "privileges": g["privileges"], "via": "schema"} for g in sch]
    out += [{"principal": g["principal"], "privileges": g["privileges"], "via": "catalog (inherited)"}
            for g in cat if set(g["privileges"]) - NON_EXPOSING_CATALOG_PRIVILEGES]
    out += [{"principal": s_owner, "privileges": ["OWNER"], "via": "schema owner"},
            {"principal": c_owner, "privileges": ["OWNER"], "via": "catalog owner"}]
    return out


def _check_staging_isolation(pv: DeployPreview, live: Live, catalog: str, schema: str, identity: str,
                             principals: Iterable[str]) -> None:
    """Staging copies may only be visible to authorised deploy / admin identities,
    including through groups and catalog-level (inherited) privileges. Anything we
    cannot establish blocks the deploy."""
    allowed = {str(p).lower() for p in principals or ()} | {str(identity).lower()}
    access = _schema_access(live, catalog, schema)
    if access is None:
        pv.block_model("staging_isolation_unknown",
                       f"who can access the staging schema {catalog}.{schema} cannot be established")
        return
    others = sorted({a["principal"] for a in access if str(a["principal"]).lower() not in allowed})
    if others:
        pv.block_model("staging_not_restricted",
                       f"the staging schema {catalog}.{schema} is accessible to principals that are not "
                       f"authorised deployers or admins: {', '.join(others)}", principals=others)
    pv.platform["staging_access"] = access


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
            planned_id = t.get("uc_object_id")
            try:
                live_id = (live.table_info(fqn) or {}).get("id")
            except Exception:
                live_id = None
            item.details.update(planned_uc_object_id=planned_id, live_uc_object_id=live_id)
            if not planned_id:
                item.block("source_identity_unrecorded",
                           f"{fqn}: the plan has no Unity Catalog id for this table: re-plan with a Databricks "
                           "connection")
            elif not live_id:
                item.block("source_identity_unreadable", f"{fqn}: its Unity Catalog id cannot be read now")
            elif live_id != planned_id:
                item.block("source_replaced",
                           f"{fqn} is a different table than at planning time (dropped and recreated?): re-plan "
                           "required")
            live_cols = {k.lower(): _norm_type(v[1]) for k, v in uc.columns.items()}
            changed = [c["name"] for c in t.get("columns") or []
                       if live_cols.get(str(c["name"]).lower()) != _norm_type(c.get("type"))]
            if changed:
                item.block("source_changed", f"{fqn}: columns missing or retyped since the plan ({', '.join(changed)}): "
                           "re-plan required", columns=changed)
            ok, err = live.can_select(fqn)
            if not ok:
                item.block("source_no_access", f"no SELECT access to {fqn}: {err}")
        if item.action == "blocked":
            for v in deps.get(ds, []):
                blocked_views.setdefault(v, []).append({"code": item.reasons[-1]["code"],
                                                        "message": item.reasons[-1]["message"]})
        pv.sources.append(item)


def _identity_owned(rec: Optional[Dict[str, Any]], info: Optional[Dict[str, Any]], kind: str) -> Tuple[bool, str, str]:
    """(owned?, code, message) -- ownership ONLY through a verified record whose UC
    object id equals the live object's id."""
    if not rec or rec.get("kind") != kind:
        return False, "name_taken", "exists and SemaBridge has no deploy record of it; it is never changed"
    if not rec.get("verified"):
        return False, "last_deploy_unverified", (f"SemaBridge's last deployment of it ended '{rec.get('outcome')}' "
                                                 "(not verified): resolve it before it is changed again")
    if info is None:
        return False, "object_unreadable", "exists but could not be read"
    if not rec.get("uc_object_id") or not info.get("id"):
        return False, "ownership_unestablished", ("no Unity Catalog object id to compare, so ownership cannot be "
                                                  "established (comments and properties are not enough)")
    if rec["uc_object_id"] != info["id"]:
        return False, "identity_changed", "is a different object than the one SemaBridge deployed"
    return True, "", ""


def _existing_stub(item: Item, live: Live, rec: Optional[Dict[str, Any]], t: Dict[str, Any]) -> None:
    fqn = item.fqn or ""
    info = live.table_info(fqn)
    ok, code, msg = _identity_owned(rec, info, "stub_table")
    if not ok:
        item.block(code, f"{fqn} {msg}")
        return
    item.details["uc_object_id"] = info.get("id")
    props = info.get("properties") or {}
    if str(props.get("semabridge.stub")).lower() != "true":
        item.block("stub_marker_missing", f"{fqn} lost its stub marker property; it is never changed")
        return
    if _cols(info.get("columns") or []) != _cols(t["columns"]):
        item.block("stub_schema_changed",
                   f"{fqn} (ours) has columns or types that differ from the approved definition; it is never altered")
        return
    rows = live.has_rows(fqn)
    if rows is None:
        item.block("stub_contents_unknown", f"whether {fqn} contains data cannot be checked")
        return
    if rows:
        item.block("stub_has_data",
                   f"{fqn} was created empty by SemaBridge but now contains data: review the binding (re-plan to bind "
                   "a real table); a populated placeholder is never treated as a verified source")
        return
    item.action = "exists_owned"


def _existing_view(item: Item, live: Live, rec: Optional[Dict[str, Any]], identity: Optional[str],
                   new_hash: str) -> None:
    fqn = item.fqn or ""
    info = live.table_info(fqn)
    ok, code, msg = _identity_owned(rec, info, "view")
    if not ok:
        item.block(code, f"{fqn} {msg}")
        return
    item.details["uc_object_id"] = info.get("id")
    if not identity or str(info.get("owner") or "").lower() != str(identity).lower():
        item.block("not_owner", f"{fqn} is owned by {info.get('owner') or 'an unknown principal'}; updating a "
                   "view's definition requires its owner")
        return
    body = D.live_body(live, fqn, info)
    if body is None:
        item.block("definition_unreadable", f"the live definition of {fqn} could not be read back")
        return
    try:
        live_hash = D.definition_hash(body)
    except D.DefinitionError as exc:
        item.block("definition_unreadable", f"the live definition of {fqn} is not a valid definition: {exc}")
        return
    item.details["live_definition_hash"] = live_hash
    if rec.get("definition_hash") and live_hash != rec["definition_hash"]:
        item.block("manual_drift", f"{fqn} was changed outside SemaBridge since the last deploy; it is not overwritten")
        return
    recorded_comment = (rec.get("details") or {}).get("comment")
    if recorded_comment is not None and (info.get("comment") or "") != recorded_comment:
        item.block("manual_drift", f"{fqn}: its Unity Catalog comment was changed outside SemaBridge; not overwritten")
        return
    item.action = "unchanged" if live_hash == new_hash else "update"
    item.details["previous_hash"] = live_hash


def _report_history(pv: DeployPreview, req: DeployRequest, history: List[Dict[str, Any]], manifest: Dict[str, Any],
                    bodies: Dict[str, str], owned: Dict[str, Dict[str, Any]], existing: Dict[str, str]) -> None:
    """Breaking interface changes (vs every name deployed so far) and unused objects."""
    from semabridge.target_backends.databricks.deploy.names import source_key

    new_views = {v["view"] for v in manifest.get("views") or []}
    old_view_by_source: Dict[str, str] = {}
    seen: Dict[Tuple[str, str, str], str] = {}
    for r in history:                                     # oldest first: latest name wins
        if r["kind"] == "view":
            old_view_by_source[r["view_source"]] = r["target_name"]
        elif r["kind"] in ("field", "measure"):
            seen[(r["view_source"], r["kind"], json.dumps(r["source_key"], sort_keys=True))] = r["target_name"]
    new_view_by_source = {str(v.get("source_dataset")): str(v.get("name")) for v in req.plan.get("views") or []}
    for src, old in sorted(old_view_by_source.items()):
        new = new_view_by_source.get(src)
        if new is None:
            pv.breaking_changes.append({"kind": "view_removed", "view": old})
        elif new != old:
            pv.breaking_changes.append({"kind": "view_renamed", "from": old, "to": new})
    new_keys: Dict[Tuple[str, str, str], Tuple[str, str]] = {}
    for v in req.plan.get("views") or []:
        src = str(v.get("source_dataset"))
        for kind, items in (("field", v.get("fields") or []), ("measure", v.get("measures") or [])):
            for it in items:
                new_keys[(src, kind, json.dumps(source_key(it.get("source_id")), sort_keys=True))] = (
                    str(v["name"]), str(it["name"]))
    for key, old_name in sorted(seen.items()):
        src, kind, _ = key
        view = old_view_by_source.get(src)
        if key not in new_keys:
            if view in new_views:
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
