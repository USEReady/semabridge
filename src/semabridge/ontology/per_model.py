"""One ontology domain per model of a multi-model project.

* :func:`build_model_domain` -- (re)build one model's domain from its OSI model,
  merging into that model's existing domain only (never another model's);
* :func:`sync_project_model_domains` -- "Sync with OSI" for every (or one)
  model of a project, each from its own latest snapshot;
* :func:`split_merged_domain` -- migration of a legacy merged project domain
  into per-model domains, carrying user-authored fields by name.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from semabridge.ontology.models import DomainOntology, model_key, per_model_domain_id, project_key
from semabridge.ontology.project_models import (
    ProjectModel,
    model_domain_name,
    osi_from_blob,
    project_label,
    project_models,
)

logger = logging.getLogger("semabridge.ontology.per_model")

SHARED_SUFFIX = "shared"


def shared_domain_id(project_id: str) -> str:
    return per_model_domain_id(project_id, SHARED_SUFFIX)


def _restamp(dom: DomainOntology, name: str) -> DomainOntology:
    """Every item's ``domain`` field names its (new) owning domain."""
    return dom.model_copy(
        update={
            "concepts": [c.model_copy(update={"domain": name}) for c in dom.concepts],
            "metrics": [m.model_copy(update={"domain": name}) for m in dom.metrics],
        }
    )


def build_model_domain(
    project_id: str,
    pm: ProjectModel,
    *,
    osi_model: Any = None,
    existing: Optional[DomainOntology] = None,
    owner: Optional[str] = None,
    report: Optional[Dict[str, List[str]]] = None,
    domain_name: Optional[str] = None,
) -> DomainOntology:
    """The domain of ONE model of a project, merged into that model's ``existing`` domain."""
    from semabridge.ontology.federated_loader import FederatedOntologyLoader

    osi = osi_model if osi_model is not None else osi_from_blob(pm.blob or {})
    name = (existing.domain if existing is not None else None) or domain_name or model_domain_name(project_id, pm.model_name)
    dom = FederatedOntologyLoader.from_osi_model(
        model=osi,
        domain_name=name,
        existing_ontology=existing,
        report=report,
        semantic_view_name=pm.semantic_view,
    )
    update: Dict[str, Any] = {
        "id": (existing.id if existing is not None and existing.id else per_model_domain_id(project_id, pm.model_id)),
        "source_project_id": project_id,
        "source_model_id": pm.model_id,
        "source_model_name": pm.model_name,
    }
    if pm.semantic_view:
        update["semantic_view_name"] = pm.semantic_view
    if owner and not dom.owner:
        update["owner"] = owner
    dom = dom.model_copy(update=update)
    return DomainOntology.model_validate(_restamp(dom, dom.domain).model_dump())


def existing_model_domain(store: Any, project_id: str, model_id: str) -> Optional[DomainOntology]:
    """The model's own domain: generated from it, attached to it, or with its per-model id."""
    mid = model_key(model_id)
    for dom in store.domains_for(project_id, mid):
        if dom.source_model_id == mid and project_key(dom.source_project_id) == project_key(project_id):
            return dom
    attached = store.get_project_attachments(project_id).get("models", {}).get(mid) or []
    for did in attached:
        dom = store.get_domain(did)
        if dom is not None:
            return dom
    return store.get_domain(per_model_domain_id(project_id, mid))


def sync_project_model_domains(
    project_id: str,
    store: Any,
    *,
    only_model: Optional[str] = None,
    owner: Optional[str] = None,
    save_to_disk: bool = True,
    repo: Any = None,
    models: Optional[List[ProjectModel]] = None,
) -> List[Dict[str, Any]]:
    """Re-sync each model's domain from that model's latest snapshot (no cross-model merging)."""
    results: List[Dict[str, Any]] = []
    wanted = model_key(only_model) if only_model else None
    for pm in models if models is not None else project_models(project_id, repo=repo):
        if wanted and pm.model_id != wanted and wanted not in pm.aliases:
            continue
        if not pm.blob:
            results.append({"model_id": pm.model_id, "model_name": pm.model_name, "skipped": "no successful snapshot yet"})
            continue
        existing = existing_model_domain(store, project_id, pm.model_id)
        rep: Dict[str, List[str]] = {}
        dom = build_model_domain(project_id, pm, existing=existing, owner=owner, report=rep)
        store.add_or_update_domain(dom, save_to_disk=save_to_disk, original=existing.domain if existing else None)
        if save_to_disk:
            store.attach_model_domain(project_id, pm.model_id, dom.id)
        results.append(
            {
                "model_id": pm.model_id,
                "model_name": pm.model_name,
                "domain": dom.domain,
                "id": dom.id,
                "semantic_view_name": dom.semantic_view_name,
                "concept_count": len(dom.concepts),
                "metric_count": len(dom.metrics),
                "relationship_count": len(dom.relationships),
                "sync_report": rep,
                "domain_obj": dom,
            }
        )
    return results


# ----------------------------------------------------------------------
# Migration: legacy merged project domain -> per-model domains
# ----------------------------------------------------------------------


def _user_authored(item: Any) -> bool:
    """True when the item carries content a person wrote (not just its BI echo)."""
    if getattr(item, "origin", None) == "user":
        return True
    own = {str(getattr(item, "name", "") or "").casefold(), str(getattr(item, "label", "") or "").casefold()}
    if any(str(s).casefold() not in own for s in getattr(item, "synonyms", None) or []):
        return True
    if getattr(item, "business_rules", None) or getattr(item, "data_steward", None) or getattr(item, "tags", None):
        return True
    desc, bi_desc = getattr(item, "description", None), getattr(item, "bi_description", None)
    return bool(desc and bi_desc and desc != bi_desc)


def _keys(item: Any) -> set:
    """Match keys of an item: name, logical name, derived identifier and their alphanumeric forms."""
    out = set()
    name = str(getattr(item, "name", None) or "").strip()
    logical = ""
    if name:
        try:
            from semabridge.ontology.federated_loader import FederatedOntologyLoader

            # The identifier a deploy derives from a BI name ("Sales $" -> SALES_DOL).
            logical = FederatedOntologyLoader._to_logical_id(name)
        except Exception:
            logical = ""
    for raw in (name, getattr(item, "logical_name", None), logical):
        text = str(raw or "").strip()
        if text:
            out.add(text.casefold())
            norm = re.sub(r"[^a-z0-9]+", "", text.casefold())
            if norm:
                out.add(norm)
    return out


def _from_hint(item: Any) -> str:
    """Model named in a generated description ("... (from Core_Finance_v1)"), case-folded."""
    m = re.search(r"\(from ([^)]+)\)", str(getattr(item, "description", None) or getattr(item, "bi_description", None) or ""))
    return m.group(1).strip().casefold() if m else ""


def _owners(item: Any, fresh_index: Dict[str, Dict[str, str]], usable: List[ProjectModel]) -> Dict[str, str]:
    """{model_id: fresh item name} of the models defining ``item`` (by name / logical name).

    When several models define it and the item's generated description names
    one of them ("(from <model>)"), only that model owns it.
    """
    keys = _keys(item)
    hits: Dict[str, str] = {}
    for pm in usable:
        idx = fresh_index[pm.model_id]
        exact = str(getattr(item, "name", "") or "").casefold()
        if exact in idx:
            hits[pm.model_id] = idx[exact]
            continue
        for k in keys:
            if k in idx:
                hits[pm.model_id] = idx[k]
                break
    if len(hits) > 1:
        hint = _from_hint(item)
        if hint:
            narrowed = {
                mid: nm for mid, nm in hits.items()
                if any(hint in (pm.model_name.casefold(), pm.model_id) for pm in usable if pm.model_id == mid)
            }
            if narrowed:
                return narrowed
    return hits


def split_merged_domain(
    project_id: str,
    merged: DomainOntology,
    models: List[ProjectModel],
) -> Tuple[List[DomainOntology], Optional[DomainOntology], Dict[str, Any]]:
    """Split ``merged`` into one domain per model (+ a shared domain for unmatched user items).

    Each model's domain is rebuilt from that model's snapshot with the merged
    domain's items OF THE SAME NAME as the existing ontology, so user-authored
    fields (descriptions, synonyms, business rules, stewards, tags, user
    origin) are carried exactly as a re-sync would. Same-named items present
    in several models are carried to each of them and reported as clashes.
    """
    report: Dict[str, Any] = {"models": {}, "clashes": [], "shared": [], "dropped_bi": [], "skipped_models": []}
    usable = [pm for pm in models if pm.blob]
    report["skipped_models"] = [pm.model_id for pm in models if not pm.blob]

    # Pass 1: which names does each model's BI define?
    osi_by_model: Dict[str, Any] = {}
    fresh_index: Dict[str, Dict[str, Dict[str, str]]] = {}
    from semabridge.ontology.federated_loader import FederatedOntologyLoader

    for pm in usable:
        osi = osi_from_blob(pm.blob or {})
        osi_by_model[pm.model_id] = osi
        fresh = FederatedOntologyLoader.from_osi_model(model=osi, domain_name=pm.model_name)
        fresh_index[pm.model_id] = {"concept": {}, "metric": {}}
        for kind, items in (("concept", fresh.concepts), ("metric", fresh.metrics)):
            for it in items:
                for k in _keys(it):
                    fresh_index[pm.model_id][kind].setdefault(k, it.name)

    def _seed_items(kind: str, items: List[Any], model_id: str) -> List[Any]:
        """Merged items owned by ``model_id``, renamed to that model's item name
        (old name kept as label/synonym) so a re-sync carries their user fields."""
        out_items: List[Any] = []
        taken: set = set()
        idx = {pm.model_id: fresh_index[pm.model_id][kind] for pm in usable}
        # Exact-name items first, so they win over a renamed duplicate.
        ordered = sorted(items, key=lambda it: 0 if str(it.name).casefold() in idx[model_id] else 1)
        for it in ordered:
            owners = _owners(it, idx, usable)
            target = owners.get(model_id)
            if not target or target.casefold() in taken:
                continue
            taken.add(target.casefold())
            if it.name != target:
                syns = list(dict.fromkeys([*(it.synonyms or []), it.name]))
                upd: Dict[str, Any] = {"name": target, "synonyms": syns}
                if hasattr(it, "label"):
                    upd["label"] = it.label or it.name
                it = it.model_copy(update=upd)
            out_items.append(it)
        return out_items

    # Pass 2: rebuild each model with the merged items it owns as "existing".
    out: List[DomainOntology] = []
    for pm in usable:
        kept_concepts = _seed_items("concept", list(merged.concepts), pm.model_id)
        kept_metrics = _seed_items("metric", list(merged.metrics), pm.model_id)
        kept_concept_names = {c.name.casefold() for c in kept_concepts}
        kept_rels = [
            r for r in merged.relationships
            if r.from_concept.casefold() in kept_concept_names and r.to_concept.casefold() in kept_concept_names
        ]
        new_name = model_domain_name(project_id, pm.model_name)
        seed = merged.model_copy(
            update={
                "domain": new_name,
                "id": per_model_domain_id(project_id, pm.model_id),
                "concepts": kept_concepts,
                "metrics": kept_metrics,
                "relationships": kept_rels,
                "policies": [],
                "semantic_view_name": pm.semantic_view,
                "source_project_id": project_id,
                "source_model_id": pm.model_id,
                "source_model_name": pm.model_name,
                "deployment_error": None,
            }
        )
        rep: Dict[str, List[str]] = {}
        dom = build_model_domain(project_id, pm, osi_model=osi_by_model[pm.model_id], existing=seed, report=rep)
        out.append(dom)
        report["models"][pm.model_id] = {
            "domain": dom.domain,
            "id": dom.id,
            "semantic_view_name": dom.semantic_view_name,
            "concepts": len(dom.concepts),
            "metrics": len(dom.metrics),
            "relationships": len(dom.relationships),
            "carried_concepts": len(kept_concepts),
            "carried_metrics": len(kept_metrics),
        }

    # Clashes: one merged item, several models defining the same name.
    owned: Dict[str, set] = {"concept": set(), "metric": set()}
    for kind, items in (("concept", merged.concepts), ("metric", merged.metrics)):
        idx = {pm.model_id: fresh_index[pm.model_id][kind] for pm in usable}
        for item in items:
            owners = _owners(item, idx, usable)
            if owners:
                owned[kind].add(id(item))
            if len(owners) > 1:
                report["clashes"].append(
                    {
                        "kind": kind,
                        "name": item.name,
                        "models": sorted(owners),
                        "user_authored": _user_authored(item),
                        "resolution": (
                            "user fields carried to every model defining it"
                            if _user_authored(item) else "kept separate per model (BI-derived)"
                        ),
                    }
                )

    # Items no current model defines: user items -> shared domain; BI leftovers are dropped.
    shared_concepts = []
    shared_metrics = []
    for c in merged.concepts:
        if id(c) in owned["concept"]:
            continue
        if c.origin == "user":
            shared_concepts.append(c)
        else:
            report["dropped_bi"].append(f"concept {c.name}")
    for m in merged.metrics:
        if id(m) in owned["metric"]:
            continue
        if m.origin == "user":
            shared_metrics.append(m)
        else:
            report["dropped_bi"].append(f"metric {m.name}")
    shared: Optional[DomainOntology] = None
    if shared_concepts or shared_metrics or merged.policies:
        sname = f"{project_label(project_id)} · shared"
        shared_concept_names = {c.name.casefold() for c in shared_concepts}
        shared = DomainOntology.model_validate(
            _restamp(
                merged.model_copy(
                    update={
                        "domain": sname,
                        "id": shared_domain_id(project_id),
                        "concepts": shared_concepts,
                        "metrics": shared_metrics,
                        "relationships": [
                            r for r in merged.relationships
                            if r.origin == "user" and r.from_concept.casefold() in shared_concept_names
                            and r.to_concept.casefold() in shared_concept_names
                        ],
                        "semantic_view_name": None,
                        "source_project_id": project_id,
                        "source_model_id": None,
                        "source_model_name": None,
                        "deployment_error": None,
                    }
                ),
                sname,
            ).model_dump()
        )
        report["shared"] = [f"concept {c.name}" for c in shared_concepts] + [f"metric {m.name}" for m in shared_metrics] + [
            f"policy {p.name}" for p in merged.policies
        ]
    return out, shared, report


def migrate_project_to_per_model(
    project_id: str,
    store: Any,
    models: List[ProjectModel],
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Idempotently replace a project's merged domain(s) by one domain per model.

    * needs at least two models with a snapshot (else nothing to split);
    * merged domain = a domain of the project without ``source_model_id``
      (other than the project's shared domain); none left -> already migrated;
    * per-model domains are written (merging into one that already exists),
      the shared domain gets unmatched user items + policies, the merged
      domain is deleted and attachments become
      ``{"models": {model_id: [id]}, "shared": [ids]}``;
    * dangling attachment entries (deleted domain files) are pruned.
    """
    result: Dict[str, Any] = {"project_id": project_id, "dry_run": dry_run, "status": "", "merged_domains": [], "reports": []}
    usable = [pm for pm in models if pm.blob]
    if len(usable) < 2:
        result["status"] = "skipped: fewer than two models with a snapshot"
        return result
    sid = shared_domain_id(project_id)
    merged_doms = [d for d in store.domains_for(project_id) if not d.source_model_id and d.id != sid]
    if not merged_doms:
        result["status"] = "already migrated"
        if not dry_run:
            result["pruned_attachments"] = store.prune_dangling_attachments()
        return result

    for merged in merged_doms:
        doms, shared, rep = split_merged_domain(project_id, merged, models)
        rep["merged_domain"] = {"domain": merged.domain, "id": merged.id, "concepts": len(merged.concepts), "metrics": len(merged.metrics)}
        result["merged_domains"].append(merged.id)
        result["reports"].append(rep)
        if dry_run:
            continue
        batch = list(doms) + ([shared] if shared is not None else [])
        for dom in batch:
            prior = store.get_domain(dom.id)
            if prior is not None and prior is not merged:
                # A per-model domain already exists (earlier sync): merge into it.
                if dom.source_model_id:
                    pm = next(p for p in usable if p.model_id == dom.source_model_id)
                    dom = build_model_domain(project_id, pm, existing=prior)
                store.add_or_update_domain(dom, save_to_disk=True, original=prior.domain, batch=batch)
            else:
                store.add_or_update_domain(dom, save_to_disk=True, batch=batch)
            if dom.source_model_id:
                store.attach_model_domain(project_id, dom.source_model_id, dom.id)
            else:
                store.attach_shared_domain(project_id, dom.id)
        store.delete_domain(merged.id)
    if not dry_run:
        result["pruned_attachments"] = store.prune_dangling_attachments()
    result["status"] = "dry run" if dry_run else "migrated"
    return result
