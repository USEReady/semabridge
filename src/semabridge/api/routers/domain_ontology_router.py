"""REST API Router for Federated Domain Ontologies.

Enables uploading multiple team-specific YAML files via UI or API, validating domain
schemas, managing domain cards, and inspecting enterprise metric collisions.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from semabridge.ontology.federated_loader import FederatedOntologyLoader
from semabridge.ontology.models import DomainOntology, FederatedEnterpriseOntology
from semabridge.ontology.store import ontology_store

logger = logging.getLogger("semabridge.api.domain_ontology_router")

router = APIRouter(prefix="/api/ontology/domains", tags=["domain_ontology"])


class ValidateYamlRequest(BaseModel):
    yaml_content: str
    domain_override: Optional[str] = None


class SaveDomainRequest(BaseModel):
    yaml_content: str


@router.get("", response_model=Dict[str, Any])
def list_domains() -> Dict[str, Any]:
    """List all federated domain ontologies with concept counts, metric counts, and collisions."""
    fed = ontology_store.get_federated_ontology()
    domain_summaries = []

    for name, dom in fed.domains.items():
        domain_summaries.append({
            "domain": name,
            "owner": dom.owner,
            "version": dom.version,
            "description": dom.description,
            "concept_count": len(dom.concepts),
            "metric_count": len(dom.metrics),
            "relationship_count": len(dom.relationships),
        })

    return {
        "domains": domain_summaries,
        "total_concepts": len(fed.concepts),
        "total_metrics": len(fed.metrics),
        "total_relationships": len(fed.relationships),
        "collisions": [c.model_dump() for c in fed.collisions],
        "unresolved_references": fed.unresolved_references,
    }


@router.get("/{domain_name}", response_model=Dict[str, Any])
def get_domain(domain_name: str) -> Dict[str, Any]:
    """Retrieve full details and raw YAML for a specific domain."""
    if domain_name not in ontology_store.domains:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_name}' not found.")

    dom = ontology_store.domains[domain_name]
    yaml_str = FederatedOntologyLoader.dump_domain_yaml(dom)

    return {
        "domain": dom.domain,
        "owner": dom.owner,
        "version": dom.version,
        "description": dom.description,
        "yaml_content": yaml_str,
        "parsed": dom.model_dump(),
    }


@router.post("/validate", response_model=Dict[str, Any])
def validate_domain_yaml(req: ValidateYamlRequest) -> Dict[str, Any]:
    """Dry-run validation of domain YAML content."""
    try:
        dom = FederatedOntologyLoader.parse_yaml_string(req.yaml_content, req.domain_override)
        return {
            "valid": True,
            "domain": dom.domain,
            "concept_count": len(dom.concepts),
            "metric_count": len(dom.metrics),
            "relationship_count": len(dom.relationships),
            "errors": [],
        }
    except Exception as e:
        return {
            "valid": False,
            "domain": req.domain_override or "Unknown",
            "concept_count": 0,
            "metric_count": 0,
            "relationship_count": 0,
            "errors": [str(e)],
        }


@router.put("/{domain_name}", response_model=Dict[str, Any])
def save_domain(domain_name: str, req: SaveDomainRequest) -> Dict[str, Any]:
    """Update or create a domain ontology from edited YAML content."""
    try:
        dom = FederatedOntologyLoader.parse_yaml_string(req.yaml_content, domain_name)
        fed = ontology_store.add_or_update_domain(dom)
        return {
            "success": True,
            "domain": dom.domain,
            "total_domains": len(fed.domains),
            "collisions": [c.model_dump() for c in fed.collisions],
        }
    except Exception as e:
        logger.error("Failed to save domain %s: %s", domain_name, e)
        raise HTTPException(status_code=400, detail=f"Invalid domain ontology: {e}")


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
) -> Dict[str, Any]:
    """Upload multiple domain YAML files via UI drag-and-drop or multipart form.
    
    Validates each file, merges into the federated enterprise ontology,
    and returns parsed results and any cross-domain collision warnings.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided.")

    uploaded_domains = []
    errors = []

    for file in files:
        filename = file.filename or "unknown.yaml"
        if not (filename.endswith(".yaml") or filename.endswith(".yml")):
            errors.append(f"Skipping {filename}: Must be a .yaml or .yml file.")
            continue

        try:
            content_bytes = await file.read()
            content_str = content_bytes.decode("utf-8")
            derived_domain = filename.split(".")[0].replace("_", " ").title()
            dom = FederatedOntologyLoader.parse_yaml_string(content_str, domain_override=derived_domain)
            ontology_store.add_or_update_domain(dom)
            uploaded_domains.append(dom.domain)
        except Exception as e:
            errors.append(f"Error processing {filename}: {str(e)}")

    fed = ontology_store.get_federated_ontology()

    return {
        "success": len(uploaded_domains) > 0,
        "uploaded_domains": uploaded_domains,
        "total_domains": len(fed.domains),
        "total_concepts": len(fed.concepts),
        "total_metrics": len(fed.metrics),
        "collisions": [c.model_dump() for c in fed.collisions],
        "unresolved_references": fed.unresolved_references,
        "errors": errors,
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
