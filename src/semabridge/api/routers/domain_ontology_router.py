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

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict

from semabridge.ontology.federated_loader import FederatedOntologyLoader, OntologyValidationError
from semabridge.ontology.store import ontology_store

logger = logging.getLogger("semabridge.api.domain_ontology_router")

router = APIRouter(prefix="/api/ontology/domains", tags=["domain_ontology"])
project_router = APIRouter(prefix="/api/ontology/projects", tags=["domain_ontology"])


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
    yaml_content: str
    # Deprecated: kept for API compatibility; only used when the YAML has no 'domain'.
    domain_override: Optional[str] = None
    # Name/id of the domain being edited (rename-aware validation).
    original_domain: Optional[str] = None


class SaveDomainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    yaml_content: str
    # Name or id of the domain being edited; a different YAML 'domain' renames it.
    original_domain: Optional[str] = None


class FromOsiRequest(BaseModel):
    osi_model: Optional[Dict[str, Any]] = None
    project_id: Optional[str] = None
    domain_name: Optional[str] = None
    save_to_disk: bool = True


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
        "collisions": [c.model_dump() for c in fed.collisions],
        "unresolved_references": fed.unresolved_references,
        "load_errors": list(ontology_store.load_errors),
    }


@router.get("/export/yaml", response_class=PlainTextResponse)
def export_unified_yaml() -> str:
    """Export the combined federated enterprise ontology as a clean YAML document."""
    fed = ontology_store.get_federated_ontology()
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
    return {
        **_summary(dom),
        "yaml_content": FederatedOntologyLoader.dump_domain_yaml(dom),
        "parsed": dom.model_dump(mode="json"),
    }


@router.post("/validate", response_model=Dict[str, Any])
def validate_domain_yaml(req: ValidateYamlRequest) -> Dict[str, Any]:
    """Dry-run validation of domain YAML content (schema + federation references)."""
    try:
        dom = FederatedOntologyLoader.parse_yaml_string(req.yaml_content, req.domain_override)
        errors = ontology_store.validate_domain(dom, original=req.original_domain)
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


def _save(yaml_content: str, original: Optional[str]) -> Dict[str, Any]:
    try:
        dom = FederatedOntologyLoader.parse_yaml_string(yaml_content)
        fed = ontology_store.add_or_update_domain(dom, original=original)
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
def create_or_save_domain(req: SaveDomainRequest) -> Dict[str, Any]:
    """Create a domain (or, with ``original_domain``, update/rename that domain)."""
    return _save(req.yaml_content, req.original_domain)


@router.put("/{domain_name}", response_model=Dict[str, Any])
def save_domain(domain_name: str, req: SaveDomainRequest) -> Dict[str, Any]:
    """Update the domain currently named (or with id) ``domain_name``; a new YAML name renames it."""
    if ontology_store.get_domain(domain_name) is None:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_name}' not found.")
    return _save(req.yaml_content, domain_name)


@router.delete("/{domain_name}", response_model=Dict[str, Any])
def delete_domain(domain_name: str) -> Dict[str, Any]:
    """Delete a domain ontology."""
    deleted = ontology_store.delete_domain(domain_name)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_name}' not found.")
    return {"success": True, "deleted_domain": domain_name}


@router.post("/upload", response_model=Dict[str, Any])
async def upload_domain_yaml_files(
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
        try:
            ontology_store.add_or_update_domain(dom, batch=parsed)
            uploaded.append({"file": names[id(dom)], "domain": dom.domain, "id": dom.id or ""})
        except Exception as e:
            errors.append({"file": names[id(dom)], "error": "; ".join(_errors_of(e))})

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
async def bootstrap_from_osi(req: FromOsiRequest) -> Dict[str, Any]:
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
            osi_obj = OSIModel(**req.osi_model)
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

    sync_report: Dict[str, List[str]] = {}
    try:
        domain_ontology = FederatedOntologyLoader.from_osi_model(
            model=osi_obj,
            domain_name=target_domain,
            existing_ontology=existing_domain_obj,
            report=sync_report,
        )
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

    return {
        "success": True,
        "domain": domain_ontology.domain,
        "id": domain_ontology.id,
        "concept_count": len(domain_ontology.concepts),
        "metric_count": len(domain_ontology.metrics),
        "relationship_count": len(domain_ontology.relationships),
        "total_domains": len(fed.domains),
        "collisions": [c.model_dump() for c in fed.collisions],
        "sync_report": sync_report,
        "yaml_content": FederatedOntologyLoader.dump_domain_yaml(domain_ontology),
    }


# ----------------------------------------------------------------------
# Project attachments
# ----------------------------------------------------------------------


@project_router.get("/{project_id}/domains", response_model=Dict[str, Any])
def get_project_domains(project_id: str) -> Dict[str, Any]:
    """Domains attached to a project (precedence order: first wins)."""
    return ontology_store.describe_project_domains(project_id)


@project_router.put("/{project_id}/domains", response_model=Dict[str, Any])
def set_project_domains(project_id: str, req: ProjectDomainsRequest) -> Dict[str, Any]:
    """Attach domains (names or ids, ordered by precedence) to a project; [] detaches all."""
    try:
        ontology_store.set_project_domains(project_id, req.domains)
    except OntologyValidationError as e:
        raise _reject(e.errors)
    return ontology_store.describe_project_domains(project_id)


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


@project_router.get("/{project_id}/knowledge-graph.sql", response_class=PlainTextResponse)
async def export_project_knowledge_graph_sql(
    project_id: str,
    database: str = "PROD_DWH",
    ontology_schema: str = "_SEMABRIDGE_ONTOLOGY",
    warehouse: str = "COMPUTE_WH",
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
    model = osi_obj.model_copy(deep=True)
    overrides = load_ontology_overrides(project_id)
    report = apply_project_ontology(
        project_id, model, model_names=[model.unique_name, model.label], overrides=overrides
    )
    emitter = OntologyEmitter(target_database=database, ontology_schema=ontology_schema, warehouse=warehouse)
    header = "".join(f"-- WARNING: {w}\n" for w in report.get("warnings", []))
    return header + emitter.emit_ontology_ddl(model, ontology_overrides=overrides)
