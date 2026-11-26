"""One OSI model from the ontology, for emitters (Databricks DDL, Genie, Fabric).

With a project id, only that project's domains are used. Without one, every
domain in the store is merged -- which only works while all projects together
stay within the single-model limits; past them the caller gets a clear 400
naming the fix (pass ``project_id``) instead of an unhandled 500.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException
from pydantic import ValidationError


def ontology_osi_model(model_name: str, project_id: Optional[str] = None) -> Any:
    from semabridge.ontology.federated_loader import FederatedOntologyLoader
    from semabridge.ontology.store import ontology_store

    if project_id:
        domains = list(ontology_store.domains_for(project_id))
        if not domains:
            raise HTTPException(status_code=404, detail=f"Project '{project_id}' has no ontology yet.")
        fed = FederatedOntologyLoader.merge_domains(domains)
    else:
        fed = ontology_store.get_federated_ontology()
    try:
        return FederatedOntologyLoader.to_osi_model(fed, model_name=model_name)
    except ValidationError as exc:
        msg = "; ".join(str(e.get("msg") or "") for e in exc.errors()) or str(exc)
        scope = f"project '{project_id}'" if project_id else "all projects together"
        hint = "" if project_id else " Pass project_id to use one project's ontology."
        raise HTTPException(status_code=400, detail=f"The ontology of {scope} cannot form one model: {msg}.{hint}") from exc
