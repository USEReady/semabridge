"""REST API Router for Federated Domain Ontologies.

Enables uploading multiple team-specific YAML files via UI or API, validating domain
schemas, managing domain cards, attaching domains to projects, and inspecting
enterprise metric collisions.

Contract highlights:
* the YAML ``domain`` key is the source of truth for a domain's name (file
  names never rename a domain; a missing ``domain`` is rejected);
* ``PUT /api/ontology/domains/{original}`` updates the domain currently named
  (or with id) ``original``; a different ``domain`` in the YAML is a rename;
* invalid content is rejected with 422 and ``detail.errors`` (one message per
  problem); an upload where every file fails returns 422 with per-file errors;
* ``GET /api/ontology/domains`` reports files that failed to load at startup
  in ``load_errors``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from semabridge.ontology.federated_loader import FederatedOntologyLoader, OntologyValidationError
from semabridge.ontology.store import ontology_store

logger = logging.getLogger("semabridge.api.domain_ontology_router")

router = APIRouter(prefix="/api/ontology/domains", tags=["domain_ontology"])
project_router = APIRouter(prefix="/api/ontology/projects", tags=["domain_ontology"])


def _require_non_empty(v: str) -> str:
    if not str(v or "").strip():
        raise ValueError("Value must not be empty")
    return v


def _request_actor(request: Optional[Request]) -> tuple[Optional[str], set[str], bool]:
    """Return (actor_label, identity_tokens_lower, is_admin) for the authenticated caller."""
    if request is None:
        return None, set(), False
    uid = getattr(request.state, "user_id", None)
    if not uid:
        return None, set(), False
    from semabridge.api.deps import get_current_user_optional
    from semabridge.api.services.project_ownership_service import _is_admin_user

    uid_str = str(uid)
    is_admin = _is_admin_user(uid_str)
    tokens: set[str] = {uid_str.casefold()}
    label = uid_str
    user = get_current_user_optional(request)
    if user is not None:
        uname = str(getattr(user, "username", "") or "").strip()
        email = str(getattr(user, "email", "") or "").strip()
        if uname:
            tokens.add(uname.casefold())
            label = uname
        if email:
            tokens.add(email.casefold())
            if label == uid_str:
                label = email
    return label, tokens, is_admin


def _enforce_domain_owner(existing: Any, request: Optional[Request]) -> Optional[str]:
    """Require domain owner or admin when auth is enabled; return authenticated label."""
    from semabridge.api.services.project_ownership_service import auth_is_enabled

    label, tokens, is_admin = _request_actor(request)
    if existing is None or not auth_is_enabled():
        return label
    if not tokens:
        raise HTTPException(status_code=403, detail="Forbidden: authentication required to modify this domain.")
    owner = str(getattr(existing, "owner", None) or "").strip()
    if is_admin or not owner or owner.casefold() in tokens:
        return label
    raise HTTPException(
        status_code=403,
        detail=f"Forbidden: only domain owner '{owner}' or an admin may modify domain '{existing.domain}'.",
    )


def _enforce_project_owner(project_id: str, request: Optional[Request]) -> None:
    if request is None:
        return
    from semabridge.api.services.project_ownership_service import auth_is_enabled, is_project_owned_by_user

    if not auth_is_enabled():
        return
    uid = getattr(request.state, "user_id", None)
    if not is_project_owned_by_user(project_id, str(uid) if uid else None, log_prefix="OntologyRouter"):
        raise HTTPException(status_code=403, detail=f"Forbidden: project '{project_id}' access denied.")


def _max_upload_bytes() -> int:
    try:
        return max(1, int(os.environ.get("SEMABRIDGE_ONTOLOGY_MAX_UPLOAD_BYTES", str(1024 * 1024))))
    except ValueError:
        return 1024 * 1024


def _errors_of(exc: Exception) -> List[str]:
    if isinstance(exc, OntologyValidationError):
        return list(exc.errors)
    return [str(exc)]


def _reject(errors: List[str], status: int = 422, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status, detail={"message": "; ".join(errors), "errors": errors, **extra})


class ValidateYamlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    yaml_content: str = Field(..., min_length=1)
    # Deprecated: kept for API compatibility; only used when the YAML has no 'domain'.
    domain_override: Optional[str] = None
    # Name/id of the domain being edited (rename-aware validation).
    original_domain: Optional[str] = None

    @field_validator("yaml_content")
    @classmethod
    def _non_empty_yaml(cls, v: str) -> str:
        return _require_non_empty(v)


class SaveDomainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    yaml_content: str = Field(..., min_length=1)
    # Name or id of the domain being edited; a different YAML 'domain' renames it.
    original_domain: Optional[str] = None

    @field_validator("yaml_content")
    @classmethod
    def _non_empty_yaml(cls, v: str) -> str:
        return _require_non_empty(v)


class FromOsiRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    osi_model: Optional[Dict[str, Any]] = None
    project_id: Optional[str] = None
    domain_name: Optional[str] = None
    save_to_disk: bool = True
    sync_existing: bool = False


class ProjectDomainsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domains: List[str]


def _summary(dom: Any) -> Dict[str, Any]:
    return {
        "domain": dom.domain,
        "id": dom.id,
        "owner": dom.owner,
        "version": dom.version,
        "description": dom.description,
        "source_project_id": getattr(dom, "source_project_id", None),
        "concept_count": len(dom.concepts),
        "metric_count": len(dom.metrics),
        "relationship_count": len(dom.relationships),
        "policy_count": len(getattr(dom, "policies", None) or []),
        "stale_count": sum(1 for c in dom.concepts if c.stale) + sum(1 for m in dom.metrics if m.stale),
    }


@router.get("", response_model=Dict[str, Any])
def list_domains() -> Dict[str, Any]:
    """List all federated domain ontologies with counts, collisions and load errors."""
    fed = ontology_store.get_federated_ontology()
    return {
        "domains": [_summary(dom) for dom in fed.domains.values()],
        "total_concepts": len(fed.concepts),
        "total_metrics": len(fed.metrics),
        "total_relationships": len(fed.relationships),
        "total_policies": len(getattr(fed, "policies", {}) or {}),
        "collisions": [c.model_dump() for c in fed.collisions],
        "unresolved_references": fed.unresolved_references,
        "load_errors": list(ontology_store.load_errors),
    }


@router.get("/export/yaml", response_class=PlainTextResponse)
def export_unified_yaml(domain: Optional[str] = Query(default=None)) -> str:
    """Export the federated enterprise ontology (or one named domain via ``?domain=``) as YAML."""
    fed = ontology_store.get_federated_ontology()
    if domain:
        target = ontology_store.get_domain(domain)
        if target is None:
            raise HTTPException(status_code=404, detail=f"Domain '{domain}' not found.")
        return FederatedOntologyLoader.dump_domain_yaml(target)
    lines = [
        "# =====================================================================",
        "# SemaBridge Unified Federated Enterprise Ontology",
        f"# Total Domains: {len(fed.domains)} | Total Concepts: {len(fed.concepts)} | Total Metrics: {len(fed.metrics)}",
        "# =====================================================================",
        "",
    ]
    for d_name, dom in fed.domains.items():
        lines.append(f"# --- Domain: {d_name} ---")
        lines.append(FederatedOntologyLoader.dump_domain_yaml(dom))
        lines.append("")
    return "\n".join(lines)


@router.get("/graph", response_model=Dict[str, Any])
def ontology_graph(project_id: Optional[str] = None) -> Dict[str, Any]:
    """Connection graph for the UI: projects -> domains -> concepts / metrics ->
    bound BI objects, plus concept relationships. Collisions, stale entries,
    unbound entries and unresolved references are flagged on the nodes.

    ``project_id`` limits the graph to the domains that project attaches.
    Declared before ``/{domain_name}`` so "graph" is not read as a domain name.
    """
    from semabridge.ontology.graph import build_ontology_graph

    fed = ontology_store.get_federated_ontology()
    return build_ontology_graph(
        fed.domains.values(),
        attachments=ontology_store._read_attachments(),
        collisions=fed.collisions,
        project_id=project_id,
    )


@router.get("/{domain_name}", response_model=Dict[str, Any])
def get_domain(domain_name: str) -> Dict[str, Any]:
    """Retrieve full details and raw YAML for a specific domain (by name or id)."""
    dom = ontology_store.get_domain(domain_name)
    if dom is None:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_name}' not found.")
    yaml_text = FederatedOntologyLoader.dump_domain_yaml(dom)
    return {
        **_summary(dom),
        "yaml_content": yaml_text,
        "yaml": yaml_text,
        "parsed": dom.model_dump(mode="json"),
    }


@router.post("/validate", response_model=Dict[str, Any])
def validate_domain_yaml(req: ValidateYamlRequest) -> Dict[str, Any]:
    """Dry-run validation of domain YAML content (schema + federation references + PBIX formula lock)."""
    try:
        dom = FederatedOntologyLoader.parse_yaml_string(req.yaml_content, req.domain_override)
        errors = ontology_store.validate_domain(
            dom,
            original=req.original_domain,
            enforce_bi_formula_readonly=True,
        )
    except Exception as e:
        return {
            "valid": False,
            "domain": req.domain_override or "Unknown",
            "concept_count": 0,
            "metric_count": 0,
            "relationship_count": 0,
            "errors": _errors_of(e),
        }
    return {
        "valid": not errors,
        "domain": dom.domain,
        "id": dom.id,
        "concept_count": len(dom.concepts),
        "metric_count": len(dom.metrics),
        "relationship_count": len(dom.relationships),
        "errors": errors,
    }


def _save(yaml_content: str, original: Optional[str], request: Optional[Request] = None) -> Dict[str, Any]:
    try:
        dom = FederatedOntologyLoader.parse_yaml_string(yaml_content)
        existing = ontology_store.get_domain(original or dom.domain)
        actor = _enforce_domain_owner(existing, request)
        if not dom.owner and actor:
            dom = dom.model_copy(update={"owner": actor})
        fed = ontology_store.add_or_update_domain(
            dom,
            original=original,
            enforce_bi_formula_readonly=True,
        )
    except HTTPException:
        raise
    except OntologyValidationError as e:
        raise _reject(e.errors)
    except Exception as e:
        logger.error("Failed to save domain %s: %s", original, e)
        raise _reject([str(e)])
    saved = ontology_store.get_domain(dom.domain) or dom
    return {
        "success": True,
        "domain": saved.domain,
        "id": saved.id,
        "renamed_from": original if (original and ontology_store.get_domain(original) is None) else None,
        "total_domains": len(fed.domains),
        "collisions": [c.model_dump() for c in fed.collisions],
    }


@router.post("", response_model=Dict[str, Any])
def create_or_save_domain(req: SaveDomainRequest, request: Request) -> Dict[str, Any]:
    """Create a domain (or, with ``original_domain``, update/rename that domain)."""
    return _save(req.yaml_content, req.original_domain, request=request)


@router.put("/{domain_name}", response_model=Dict[str, Any])
def save_domain(domain_name: str, req: SaveDomainRequest, request: Request) -> Dict[str, Any]:
    """Update the domain currently named (or with id) ``domain_name``; a new YAML name renames it."""
    if ontology_store.get_domain(domain_name) is None:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_name}' not found.")
    return _save(req.yaml_content, domain_name, request=request)


class MetricMetadataUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: Optional[str] = None
    synonyms: Optional[List[str]] = None
    data_steward: Optional[str] = None
    business_rules: Optional[List[str]] = None


@router.patch("/{domain_name}/metrics/{metric_name}/metadata", response_model=Dict[str, Any])
def update_metric_metadata(
    domain_name: str,
    metric_name: str,
    req: MetricMetadataUpdateRequest,
    request: Request,
) -> Dict[str, Any]:
    """Update business metadata (description, synonyms, owner, rules) for one metric without altering formulas."""
    dom = ontology_store.get_domain(domain_name)
    if dom is None:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_name}' not found.")
    _enforce_domain_owner(dom, request)

    target_cf = metric_name.strip().casefold()
    updated_metrics = []
    matched = None
    for m in dom.metrics:
        if m.name.casefold() == target_cf:
            updates: Dict[str, Any] = {}
            if req.description is not None:
                updates["description"] = req.description.strip() or None
            if req.synonyms is not None:
                updates["synonyms"] = req.synonyms
            if req.data_steward is not None:
                updates["data_steward"] = req.data_steward.strip() or None
            if req.business_rules is not None:
                updates["business_rules"] = req.business_rules
            matched = m.model_copy(update=updates)
            updated_metrics.append(matched)
        else:
            updated_metrics.append(m)

    if matched is None:
        raise HTTPException(status_code=404, detail=f"Metric '{metric_name}' not found in domain '{dom.domain}'.")

    try:
        updated_dom = dom.model_copy(update={"metrics": updated_metrics})
        ontology_store.add_or_update_domain(
            updated_dom,
            original=dom.domain,
            enforce_bi_formula_readonly=True,
        )
    except OntologyValidationError as e:
        raise _reject(e.errors)

    return {"success": True, "domain": dom.domain, "metric": matched.model_dump(mode="json")}


@router.delete("/{domain_name}", response_model=Dict[str, Any])
def delete_domain(domain_name: str, request: Request) -> Dict[str, Any]:
    """Delete a domain ontology."""
    existing = ontology_store.get_domain(domain_name)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_name}' not found.")
    _enforce_domain_owner(existing, request)
    ontology_store.delete_domain(domain_name)
    return {"success": True, "deleted_domain": domain_name}


@router.post("/upload", response_model=Dict[str, Any])
async def upload_domain_yaml_files(
    request: Request,
    files: List[UploadFile] = File(...),
) -> Any:
    """Upload multiple domain YAML files (multipart).

    Each file's ``domain`` key names its domain. Files are validated together
    (cross-file relationships resolve within the batch). Returns 200 with
    per-file ``errors`` if at least one file was ingested, 422 if none was.
    """
    if not files:
        raise _reject(["No files provided."], status=400)

    limit = _max_upload_bytes()
    parsed: List[Any] = []
    names: Dict[int, str] = {}
    errors: List[Dict[str, str]] = []

    for file in files:
        filename = os.path.basename(file.filename or "") or "(unnamed)"
        if not filename.lower().endswith((".yaml", ".yml")):
            errors.append({"file": filename, "error": "Must be a .yaml or .yml file."})
            continue
        content_bytes = await file.read(limit + 1)
        if len(content_bytes) > limit:
            errors.append({"file": filename, "error": f"File exceeds the {limit} byte upload limit."})
            continue
        try:
            content_str = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            errors.append({"file": filename, "error": "File is not valid UTF-8 text."})
            continue
        try:
            dom = FederatedOntologyLoader.parse_yaml_string(content_str)
        except Exception as e:
            errors.append({"file": filename, "error": "; ".join(_errors_of(e))})
            continue
        if any(p.domain.casefold() == dom.domain.casefold() for p in parsed):
            errors.append({"file": filename, "error": f"Domain '{dom.domain}' is declared by more than one uploaded file."})
            continue
        parsed.append(dom)
        names[id(dom)] = filename

    uploaded: List[Dict[str, str]] = []
    for dom in parsed:
        orig_file = names[id(dom)]
        try:
            existing = ontology_store.get_domain(dom.domain)
            actor = _enforce_domain_owner(existing, request)
            if not dom.owner and actor:
                dom = dom.model_copy(update={"owner": actor})
            ontology_store.add_or_update_domain(
                dom,
                batch=parsed,
                enforce_bi_formula_readonly=True,
            )
            uploaded.append({"file": orig_file, "domain": dom.domain, "id": dom.id or ""})
        except HTTPException as he:
            errors.append({"file": orig_file, "error": str(he.detail)})
        except Exception as e:
            errors.append({"file": orig_file, "error": "; ".join(_errors_of(e))})

    fed = ontology_store.get_federated_ontology()
    body = {
        "success": bool(uploaded),
        "uploaded_domains": [u["domain"] for u in uploaded],
        "uploaded": uploaded,
        "total_domains": len(fed.domains),
        "total_concepts": len(fed.concepts),
        "total_metrics": len(fed.metrics),
        "collisions": [c.model_dump() for c in fed.collisions],
        "unresolved_references": fed.unresolved_references,
        "errors": [f"{e['file']}: {e['error']}" for e in errors],
        "file_errors": errors,
    }
    if not uploaded:
        return JSONResponse(status_code=422, content={"detail": {"message": "No ontology file was ingested.", **body}})
    return body


@router.post("/from-osi", response_model=Dict[str, Any])
async def bootstrap_from_osi(req: FromOsiRequest, request: Request) -> Dict[str, Any]:
    """Bootstrap or re-sync a domain ontology from an OSI model or project snapshot.

    Re-sync MERGES: BI-derived items are refreshed, user-added items and user
    edits are kept, new BI items are added, vanished BI items are marked stale
    (reported in ``sync_report.stale``) instead of being deleted.
    """
    from semabridge.intermediate.models import OSIModel

    osi_obj: Optional[OSIModel] = None

    # "Sync with OSI" on an existing domain may omit project_id: use the
    # project the domain was bootstrapped from.
    project_id = req.project_id
    if not req.osi_model and not project_id and req.domain_name:
        _existing = ontology_store.get_domain(req.domain_name)
        project_id = getattr(_existing, "source_project_id", None) if _existing else None
        if not project_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Domain '{req.domain_name}' has no source project to sync from; "
                    "provide 'project_id'."
                ),
            )

    if req.osi_model:
        try:
            # Use Pydantic model_validate_json to safely parse nested dictionaries and date strings
            import json as _json
            osi_obj = OSIModel.model_validate_json(_json.dumps(req.osi_model, default=str))
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid OSIModel payload: {e}")
    elif project_id:
        from semabridge.api.routers.export_router import _load_osi_model
        osi_obj = await _load_osi_model(project_id)
        if not osi_obj:
            raise HTTPException(status_code=404, detail=f"No OSI model found for project '{project_id}'.")
    else:
        raise HTTPException(status_code=400, detail="Must provide either 'osi_model' dictionary or 'project_id'.")

    target_domain = req.domain_name or osi_obj.label or osi_obj.unique_name or "Enterprise"
    existing_domain_obj = ontology_store.get_domain(target_domain)
    existing_for_project = None
    if project_id:
        fed = ontology_store.get_federated_ontology()
        for d in fed.domains.values():
            if (getattr(d, "source_project_id", None) or "").strip().casefold() == project_id.strip().casefold():
                existing_for_project = d
                break

    # Block creating a second domain with a different name when the project already has one, unless syncing
    if (
        existing_for_project is not None
        and not existing_domain_obj
        and req.domain_name
        and req.domain_name.strip().casefold() != existing_for_project.domain.strip().casefold()
        and not req.sync_existing
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Project '{project_id}' already has domain '{existing_for_project.domain}'. "
                "Use Sync from OSI (sync_existing=true) to update the existing domain instead of creating a duplicate."
            ),
        )

    # When domain display name has been renamed by the user, reuse the project's existing domain in place
    if not existing_domain_obj and existing_for_project is not None:
        existing_domain_obj = existing_for_project
        target_domain = existing_for_project.domain

    actor = _enforce_domain_owner(existing_domain_obj, request)
    dep_info: Dict[str, Optional[str]] = {}
    if project_id:
        _enforce_project_owner(project_id, request)
        try:
            from semabridge.repository.model_repository import ModelRepository
            dep_info = ModelRepository().get_project_deployment_info(project_id)
        except Exception:
            dep_info = {}

    sync_report: Dict[str, List[str]] = {}
    try:
        domain_ontology = FederatedOntologyLoader.from_osi_model(
            model=osi_obj,
            domain_name=target_domain,
            existing_ontology=existing_domain_obj,
            report=sync_report,
            semantic_view_ddl=dep_info.get("ddl"),
            semantic_view_name=dep_info.get("semantic_view"),
            deployment_error=dep_info.get("deploy_error"),
        )
        if not domain_ontology.owner and actor:
            domain_ontology = domain_ontology.model_copy(update={"owner": actor})
        if project_id:
            domain_ontology.source_project_id = project_id
        elif existing_domain_obj is not None and getattr(existing_domain_obj, "source_project_id", None):
            domain_ontology.source_project_id = existing_domain_obj.source_project_id
        fed = ontology_store.add_or_update_domain(
            domain_ontology,
            save_to_disk=req.save_to_disk,
            original=existing_domain_obj.domain if existing_domain_obj else None,
        )
    except OntologyValidationError as e:
        raise _reject(e.errors)

    yaml_text = FederatedOntologyLoader.dump_domain_yaml(domain_ontology)
    return {
        "success": True,
        "domain": domain_ontology.domain,
        "id": domain_ontology.id,
        "concept_count": len(domain_ontology.concepts),
        "metric_count": len(domain_ontology.metrics),
        "relationship_count": len(domain_ontology.relationships),
        "total_domains": len(fed.domains),
        "collisions": [c.model_dump() for c in fed.collisions],
        "sync_report": sync_report,  # hardcoding-ok: API response field
        "yaml_content": yaml_text,
        "yaml": yaml_text,
    }


# ----------------------------------------------------------------------
# Project attachments
# ----------------------------------------------------------------------


@project_router.get("/{project_id}/domains", response_model=Dict[str, Any])
def get_project_domains(project_id: str) -> Dict[str, Any]:
    """Domains attached to a project (precedence order: first wins)."""
    return ontology_store.describe_project_domains(project_id)


@project_router.put("/{project_id}/domains", response_model=Dict[str, Any])
def set_project_domains(project_id: str, req: ProjectDomainsRequest, request: Request) -> Dict[str, Any]:
    """Attach domains (names or ids, ordered by precedence) to a project; [] detaches all."""
    _enforce_project_owner(project_id, request)
    try:
        ontology_store.set_project_domains(project_id, req.domains)
    except OntologyValidationError as e:
        raise _reject(e.errors)
    return ontology_store.describe_project_domains(project_id)


class ConflictResolutionRequest(BaseModel):
    # 'priority' (ordered domains decide) | 'namespace' (keep all, prefixed by domain) | None (clear)
    mode: Optional[str] = None
    # Optional precedence order (domain names or ids); reorders the project's attachments.
    priority: Optional[List[str]] = None


def _conflict_resolution_state(project_id: str) -> Dict[str, Any]:
    from semabridge.ontology.apply import _formula_conflicts

    domains = ontology_store.get_project_domains(project_id)
    return {
        "project_id": project_id,
        "mode": ontology_store.get_conflict_resolution(project_id),
        "options": ["priority", "namespace"],
        "priority": [d.domain for d in domains],
        "conflicts": _formula_conflicts(domains),
    }


@project_router.get("/{project_id}/conflict-resolution", response_model=Dict[str, Any])
def get_project_conflict_resolution(project_id: str) -> Dict[str, Any]:
    """Cross-domain metric formula conflicts of the project's attached domains and the saved choice."""
    return _conflict_resolution_state(project_id)


@project_router.put("/{project_id}/conflict-resolution", response_model=Dict[str, Any])
def set_project_conflict_resolution(
    project_id: str, req: ConflictResolutionRequest, request: Request
) -> Dict[str, Any]:
    """Save how conflicting metric definitions across attached domains are resolved.

    ``priority`` keeps the definition of the first domain in ``priority`` order
    (the project's attachment order is updated to match); ``namespace`` keeps
    every definition, prefixed by its domain. Used by dry runs and deploys.
    """
    _enforce_project_owner(project_id, request)
    try:
        if req.priority:
            current = ontology_store.get_project_domain_ids(project_id)
            ordered = []
            for item in req.priority:
                dom = ontology_store.get_domain(str(item))
                if dom is None or dom.id not in current:
                    raise OntologyValidationError([f"Domain '{item}' is not attached to project '{project_id}'."])
                if dom.id not in ordered:
                    ordered.append(dom.id)
            ordered += [i for i in current if i not in ordered]
            ontology_store.set_project_domains(project_id, ordered)
        ontology_store.set_conflict_resolution(project_id, req.mode)
    except OntologyValidationError as e:
        raise _reject(e.errors)
    return _conflict_resolution_state(project_id)


@project_router.post("/{project_id}/preview", response_model=Dict[str, Any])
async def preview_project_ontology(project_id: str) -> Dict[str, Any]:
    """Dry-run the ontology against the project's latest OSI model: what matches, what doesn't."""
    from semabridge.api.routers.export_router import _load_osi_model
    from semabridge.ontology.apply import apply_project_ontology

    osi_obj = await _load_osi_model(project_id)
    if not osi_obj:
        raise HTTPException(status_code=404, detail=f"No OSI model found for project '{project_id}'.")
    model = osi_obj.model_copy(deep=True)
    return apply_project_ontology(project_id, model, model_names=[model.unique_name, model.label])


def _project_kg_target(project_id: str) -> Dict[str, Optional[str]]:
    """The project's real deployment database / schema / semantic view / warehouse."""
    out: Dict[str, Optional[str]] = {}
    try:
        from semabridge.repository.model_repository import ModelRepository

        info = ModelRepository().get_project_deployment_info(project_id)
        out.update({k: info.get(k) for k in ("database", "schema", "semantic_view")})
    except Exception as exc:
        logger.warning("Deployment info unavailable for project %s: %s", project_id, exc)
    try:
        from semabridge.ai.project_connection import resolve_project_warehouse_config

        cfg = resolve_project_warehouse_config(project_id, "SNOWFLAKE")
        out["warehouse"] = getattr(cfg, "warehouse", None) if cfg is not None else None
        if not out.get("database"):
            out["database"] = getattr(cfg, "database", None) if cfg is not None else None
    except Exception as exc:
        logger.warning("Snowflake target of project %s unavailable: %s", project_id, exc)
    return out


@project_router.get("/{project_id}/knowledge-graph.sql", response_class=PlainTextResponse)
async def export_project_knowledge_graph_sql(
    project_id: str,
    database: Optional[str] = None,
    ontology_schema: str = "_SEMABRIDGE_ONTOLOGY",
    warehouse: Optional[str] = None,
) -> str:
    """Snowflake knowledge-graph + Cortex Search DDL for the project's ontology.

    Companion artifact to the semantic view: the project's OSI model with its
    ontology applied (same precedence as a migration run) compiled by
    OntologyEmitter into ONTOLOGY_CONCEPTS / ONTOLOGY_METRIC_BINDINGS tables and
    Cortex Search services. Returned as text for review / manual execution.
    """
    from semabridge.api.routers.export_router import _load_osi_model
    from semabridge.connectors.ontology_emitter import OntologyEmitter
    from semabridge.ontology.apply import apply_project_ontology
    from semabridge.utils.synonyms import load_ontology_overrides

    osi_obj = await _load_osi_model(project_id)
    if not osi_obj:
        raise HTTPException(status_code=404, detail=f"No OSI model found for project '{project_id}'.")
    target = _project_kg_target(project_id)
    database = (database or "").strip() or target.get("database")
    warehouse = (warehouse or "").strip() or target.get("warehouse")
    missing = [n for n, v in (("database", database), ("warehouse", warehouse)) if not v]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Project '{project_id}' has no deployment {' / '.join(missing)} configured. "
                "Deploy the project (or set its Snowflake target), or pass "
                + " and ".join(f"'?{m}='" for m in missing) + " explicitly."
            ),
        )
    model = osi_obj.model_copy(deep=True)
    overrides = load_ontology_overrides(project_id)
    report = apply_project_ontology(
        project_id, model, model_names=[model.unique_name, model.label], overrides=overrides
    )
    emitter = OntologyEmitter(
        target_database=database,
        target_schema=target.get("schema"),
        ontology_schema=ontology_schema,
        warehouse=warehouse,
        semantic_view=target.get("semantic_view"),
    )
    header = "".join(f"-- WARNING: {w}\n" for w in report.get("warnings", []))
    return header + emitter.emit_ontology_ddl(model, ontology_overrides=overrides)


# ----------------------------------------------------------------------
# Ontology health per project (Projects list / project Overview)
# ----------------------------------------------------------------------


def _domains_for_project(project_id: str, fed: Any) -> List[Any]:
    """Domains attached to the project, plus domains bootstrapped from it."""
    out: List[Any] = []
    seen: set = set()
    for dom in ontology_store.get_project_domains(project_id):
        if dom.id not in seen:
            seen.add(dom.id)
            out.append(dom)
    for dom in fed.domains.values():
        if getattr(dom, "source_project_id", None) == project_id and dom.id not in seen:
            seen.add(dom.id)
            out.append(dom)
    return out


@project_router.get("/health", response_model=Dict[str, Any])
def get_projects_ontology_health(project_ids: str = "") -> Dict[str, Any]:
    """Ontology health for several projects at once: ``?project_ids=a,b,c``."""
    from semabridge.ontology.quality import project_ontology_health

    fed = ontology_store.get_federated_ontology()
    ids = [p.strip() for p in project_ids.split(",") if p.strip()][:500]
    return {
        "projects": {pid: project_ontology_health(_domains_for_project(pid, fed), fed) for pid in ids}
    }


@project_router.get("/{project_id}/health", response_model=Dict[str, Any])
def get_project_ontology_health(project_id: str) -> Dict[str, Any]:
    from semabridge.ontology.quality import project_ontology_health

    fed = ontology_store.get_federated_ontology()
    return {"project_id": project_id, **project_ontology_health(_domains_for_project(project_id, fed), fed)}


# ----------------------------------------------------------------------
# New-project wizard: Ontology review BEFORE the dry run
# ----------------------------------------------------------------------

review_router = APIRouter(prefix="/api/ontology/review", tags=["domain_ontology"])


class ReviewAnalyzeRequest(BaseModel):
    source_config: Dict[str, Any]
    selected_sources: List[str] = []
    project_id: Optional[str] = None


class ReviewMerge(BaseModel):
    name: str
    members: List[str]


class ReviewConflict(BaseModel):
    name: str
    resolution: str = "defer"  # use_existing | keep_new | defer
    new_name: Optional[str] = None


class ReviewApplyRequest(BaseModel):
    analysis_id: Optional[str] = None
    source_config: Optional[Dict[str, Any]] = None
    selected_sources: List[str] = []
    project_id: Optional[str] = None
    domain_name: str
    owner: Optional[str] = None
    merges: List[ReviewMerge] = []
    conflicts: List[ReviewConflict] = []
    descriptions: Dict[str, str] = {}


def _find_project_domain(project_id: Optional[str]) -> Any:
    """Return the domain whose ``source_project_id`` matches ``project_id`` (case-insensitive)."""
    pid = str(project_id or "").strip().casefold()
    if not pid:
        return None
    for d in ontology_store.get_federated_ontology().domains.values():
        if (getattr(d, "source_project_id", None) or "").strip().casefold() == pid:
            return d
    return None


def _review_access(request: Any, project_id: Optional[str]) -> None:
    from semabridge.api.services.project_ownership_service import (
        auth_is_enabled,
        is_project_owned_by_user,
        require_request_user_id,
    )

    user_id = require_request_user_id(request)
    pid = str(project_id or "").strip()
    if pid and pid != "preview" and auth_is_enabled() and not is_project_owned_by_user(pid, user_id):
        raise HTTPException(status_code=403, detail="Forbidden: project access denied")


@review_router.post("/analyze", response_model=Dict[str, Any])
async def analyze_sources_for_review(req: ReviewAnalyzeRequest, request: Request) -> Dict[str, Any]:
    """Read the selected source models and report duplicates, conflicts and glossary gaps."""
    from semabridge.api.services.ontology_review_service import UnsupportedSourceError, run_analysis

    _review_access(request, req.project_id)
    try:
        result = await run_analysis(req.source_config, req.selected_sources, req.project_id)
    except UnsupportedSourceError as exc:
        return {"supported": False, "message": str(exc)}
    if result.get("summary") is None:
        errors = result.get("errors") or []
        reasons = "; ".join(f"{e.get('source')}: {e.get('error')}" for e in errors[:3])
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"None of the selected models could be read ({reasons})." if reasons else "None of the selected models could be read.",
                "errors": errors,
            },
        )
    return {"supported": True, **result}


@review_router.post("/apply", response_model=Dict[str, Any])
async def apply_review(req: ReviewApplyRequest, request: Request) -> Dict[str, Any]:
    """Save the reviewed ontology as a NEW domain (and attach it when project_id is given)."""
    from semabridge.api.services.ontology_review_service import (
        UnsupportedSourceError,
        build_reviewed_domain,
        cache_get,
        read_source_models,
    )
    from semabridge.ontology.models import DomainOntology

    _review_access(request, req.project_id)
    name = req.domain_name.strip()
    if not name:
        raise _reject(["Domain name is required."])
    real_project = bool(req.project_id and req.project_id != "preview")
    # One domain per project (same rule as /from-osi): when the project already
    # owns a domain, the review is merged into it instead of creating a second
    # domain under a different name.
    existing_for_project = _find_project_domain(req.project_id) if real_project else None
    if existing_for_project is not None:
        name = existing_for_project.domain
        _enforce_domain_owner(existing_for_project, request)
    elif ontology_store.get_domain(name) is not None:
        raise _reject(
            [f"Domain '{name}' already exists. Choose another name, or attach the existing domain to the project."],
            status=409,
        )

    models = cache_get(req.analysis_id)
    if models is None:
        if not req.source_config:
            raise _reject(["The review expired; run the analysis again."], status=410)
        try:
            models, _errors = await read_source_models(req.source_config, req.selected_sources, req.project_id)
        except UnsupportedSourceError as exc:
            raise _reject([str(exc)])
        if not models:
            raise _reject(["None of the selected models could be read."])

    try:
        draft, report = build_reviewed_domain(
            models,
            domain_name=name,
            owner=req.owner,
            merges=[m.model_dump() for m in req.merges],
            conflicts=[c.model_dump() for c in req.conflicts],
            descriptions=req.descriptions,
            existing=existing_for_project,
        )
        draft = DomainOntology.model_validate(draft.model_dump())
        if real_project:
            draft.source_project_id = req.project_id
        fed = ontology_store.add_or_update_domain(
            draft,
            save_to_disk=True,
            original=existing_for_project.domain if existing_for_project is not None else None,
        )
        if real_project:
            ids = ontology_store.get_project_domain_ids(req.project_id)
            if draft.id not in ids:
                ontology_store.set_project_domains(req.project_id, [*ids, str(draft.id)])
    except OntologyValidationError as exc:
        raise _reject(exc.errors)
    except ValueError as exc:
        raise _reject([str(exc)])

    return {
        "success": True,
        "domain": draft.domain,
        "id": draft.id,
        "synced_existing": existing_for_project is not None,
        "requested_domain_name": req.domain_name.strip(),
        "metric_count": len(draft.metrics),
        "concept_count": len(draft.concepts),
        "report": report,
        "collisions": [c.model_dump() for c in fed.collisions if draft.domain in c.conflicting_domains],
    }


class SnowflakeMetricCandidate(BaseModel):
    name: str
    label: Optional[str] = None
    expression: Optional[str] = None


class SnowflakeSimilarityRequest(BaseModel):
    project_id: Optional[str] = None
    metrics: List[SnowflakeMetricCandidate] = Field(default_factory=list)


@review_router.post("/snowflake-similarity", response_model=Dict[str, Any])
def check_snowflake_metric_similarity(req: SnowflakeSimilarityRequest, request: Request) -> Dict[str, Any]:
    """Compare incoming model metrics against existing Snowflake Semantic Views and ontology."""
    import re
    from difflib import SequenceMatcher
    from semabridge.ai.project_connection import resolve_project_warehouse_config
    from semabridge.connectors.snowflake_connection import get_snowflake_connect_kwargs

    _review_access(request, req.project_id)
    uid = getattr(request.state, "user_id", None)
    owner_id = str(uid) if uid else None

    existing_items: List[Dict[str, str]] = []
    sf_connected = False
    sf_error: Optional[str] = None
    views_scanned = 0

    try:
        sf = resolve_project_warehouse_config(req.project_id, "SNOWFLAKE", owner_id=owner_id)
        if sf is not None:
            kw = get_snowflake_connect_kwargs(sf)
            if kw.get("account") and kw.get("user"):
                import snowflake.connector

                conn = snowflake.connector.connect(**kw)
                try:
                    cur = conn.cursor()
                    db = getattr(sf, "database", None)
                    sch = getattr(sf, "schema_name", None)
                    scope_clause = f' IN SCHEMA "{db}"."{sch}"' if (db and sch) else (f' IN DATABASE "{db}"' if db else "")
                    cur.execute(f"SHOW SEMANTIC VIEWS{scope_clause}")
                    cols = [d[0].lower() for d in cur.description] if cur.description else []
                    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                    for vr in rows[:10]:
                        v_db = vr.get("database_name") or db or ""
                        v_sch = vr.get("schema_name") or sch or ""
                        v_name = vr.get("name") or ""
                        if not v_name:
                            continue
                        fqn = ".".join(p for p in (v_db, v_sch, v_name) if p)
                        views_scanned += 1
                        try:
                            cur.execute(f"DESCRIBE SEMANTIC VIEW {fqn}")
                            dcols = [d[0].lower() for d in cur.description] if cur.description else []
                            for dr in cur.fetchall():
                                rec = dict(zip(dcols, dr))
                                kind = str(rec.get("object_kind") or rec.get("kind") or "").upper()
                                obj_name = str(rec.get("object_name") or rec.get("name") or "").strip()
                                prop = str(rec.get("property") or "").upper()
                                prop_val = str(rec.get("property_value") or "").strip()
                                if kind in ("METRIC", "METRICS", "") and obj_name:
                                    existing_items.append({
                                        "metric": obj_name,
                                        "view": fqn,
                                        "expression": prop_val if prop == "EXPRESSION" else "",
                                        "source": "snowflake",
                                    })
                        except Exception:
                            existing_items.append({"metric": v_name, "view": fqn, "expression": "", "source": "snowflake_view"})
                    sf_connected = True
                finally:
                    conn.close()
    except Exception as exc:
        sf_error = str(exc)

    # Supplement with deployed ontology metrics
    fed = ontology_store.get_federated_ontology()
    for dname, dom in fed.domains.items():
        sv = getattr(dom, "semantic_view_name", None) or dname
        for m in dom.metrics:
            existing_items.append({
                "metric": m.name,
                "view": sv,
                "expression": m.sql_expression or m.dax_expression or "",
                "source": "ontology",
            })

    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", (s or "").lower())

    results: List[Dict[str, Any]] = []
    for cand in req.metrics:
        cname = cand.label or cand.name
        cnorm = _norm(cname)
        cexpr_norm = _norm(cand.expression or "")
        best_score = 0.0
        best_hit: Optional[Dict[str, str]] = None
        status = "NEW"

        for ex in existing_items:
            enorm = _norm(ex["metric"])
            eexpr_norm = _norm(ex.get("expression", ""))
            if cnorm and enorm and cnorm == enorm:
                best_score = 1.0
                best_hit = ex
                status = "EXACT_MATCH"
                break
            if cexpr_norm and eexpr_norm and cexpr_norm == eexpr_norm:
                best_score = 0.98
                best_hit = ex
                status = "EXACT_MATCH"
                break
            score = SequenceMatcher(None, cnorm, enorm).ratio() if (cnorm and enorm) else 0.0
            if score > best_score:
                best_score = score
                best_hit = ex

        if status != "EXACT_MATCH" and best_score >= 0.65 and best_hit is not None:
            status = "SIMILAR_EXISTS"

        results.append({
            "name": cand.name,
            "label": cname,
            "status": status,
            "similarity_pct": round(best_score * 100),
            "matched_metric": best_hit["metric"] if (best_hit and status != "NEW") else None,
            "matched_view": best_hit["view"] if (best_hit and status != "NEW") else None,
            "matched_source": best_hit["source"] if (best_hit and status != "NEW") else None,
        })

    return {
        "snowflake_connected": sf_connected,
        "snowflake_error": sf_error,
        "views_scanned": views_scanned,
        "matches": results,
    }
