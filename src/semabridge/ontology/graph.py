"""Connection graph of the federated ontology, for the UI "Connection Map".

Nodes:
- ``project``  a project that attaches one or more domains
- ``domain``   a domain ontology file
- ``concept``  a business concept (entity), with its attributes and their bindings
- ``metric``   a business metric
- ``bi``       a BI model object a concept or metric is bound to via ``maps_to``
               (table, ``Table.Column`` or measure name)

Edges (``kind``):
- ``uses``       project -> domain (``order`` = precedence, 1 = highest)
- ``defines``    domain -> concept / metric
- ``measures``   metric -> concept (the metric's ``concept``)
- ``relates``    concept -> concept (domain relationship; label = predicate or cardinality)
- ``binds``      concept / metric -> bi object (``maps_to``)

Problems are reported on the nodes, never hidden: ``collision`` (same name
defined differently in several domains), ``stale`` (vanished from the BI
model on the last re-sync), ``unbound`` (no ``maps_to``; the entry only
matches by exact name or synonym at run time) and ``unresolved`` (a metric's
concept or a relationship end that no domain defines).
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple


def _key(text: Any) -> str:
    return str(text or "").strip().casefold()


def _norm_metric_key(text: Any) -> str:
    return re.sub(r"[\s_\-]+", "", str(text or "").strip().casefold())


def build_ontology_graph(
    domains: Iterable[Any],
    attachments: Optional[Dict[str, List[str]]] = None,
    collisions: Optional[Iterable[Any]] = None,
    project_names: Optional[Dict[str, str]] = None,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build ``{"nodes": [...], "edges": [...], "summary": {...}}``.

    ``project_id`` limits the graph to that project's attached domains.
    """
    domains = [d for d in domains if d is not None]
    attachments = {str(k): list(v or []) for k, v in (attachments or {}).items()}
    project_names = project_names or {}

    if project_id is not None:
        pid_clean = str(project_id).strip()
        pid_cf = pid_clean.casefold()

        def _slug(s: Any) -> str:
            val = str(s or "").strip().casefold()
            val = re.sub(r"^proj[-_]", "", val)
            return re.sub(r"[\s_\-]+", " ", val).strip()

        pid_slug = _slug(pid_clean)

        # 1. Direct or case-insensitive match in attachments
        keep_ids = set()
        matched_pids = set()
        for k, v in attachments.items():
            k_clean = str(k).strip()
            p_name = str(project_names.get(k_clean) or "").strip()
            if (
                k_clean.casefold() == pid_cf
                or p_name.casefold() == pid_cf
                or k_clean == pid_clean
                or (pid_slug and _slug(k_clean) == pid_slug)
                or (pid_slug and _slug(p_name) == pid_slug)
            ):
                keep_ids.update(v or [])
                matched_pids.add(k_clean)

        # 2. Metadata match: source_project_id, id, or domain name
        for d in domains:
            sp = str(getattr(d, "source_project_id", "") or "").strip()
            did = str(getattr(d, "id", "") or "").strip()
            dname = str(getattr(d, "domain", "") or "").strip()
            did_orig = str(getattr(d, "id", None) or getattr(d, "domain", ""))

            sp_slug = _slug(sp)
            did_slug = _slug(did)
            dname_slug = _slug(dname)

            is_match = (
                pid_cf in (sp.casefold(), did.casefold(), dname.casefold())
                or (sp and sp.casefold() == pid_cf)
                or (pid_slug and pid_slug in (sp_slug, did_slug, dname_slug))
                or (pid_slug and (sp_slug == pid_slug or did_slug == pid_slug or dname_slug == pid_slug))
                or (pid_slug and (dname_slug.startswith(pid_slug) or pid_slug.startswith(dname_slug)))
                or any(
                    str(project_names.get(p, "")).casefold() == pid_cf or _slug(project_names.get(p, "")) == pid_slug
                    for p in [sp, did]
                    if p
                )
            )
            if is_match:
                keep_ids.add(did_orig)
                keep_ids.add(getattr(d, "id", ""))
                keep_ids.add(getattr(d, "domain", ""))
                if sp:
                    matched_pids.add(sp)

        domains = [
            d
            for d in domains
            if getattr(d, "id", None) in keep_ids
            or getattr(d, "domain", None) in keep_ids
            or (getattr(d, "source_project_id", "") or "").strip().casefold() == pid_cf
            or (_slug(getattr(d, "source_project_id", "")) == pid_slug if pid_slug else False)
        ]

        # Ensure project node and attachment edges are established
        target_pid = list(matched_pids)[0] if matched_pids else pid_clean
        if domains:
            resolved_dom_ids = [str(getattr(d, "id", None) or getattr(d, "domain", "")) for d in domains]
            attachments = {target_pid: resolved_dom_ids}
        else:
            attachments = {target_pid: list(keep_ids)}

    collided: Dict[Tuple[str, str], List[str]] = {}
    for c in collisions or []:
        item_type = _key(getattr(c, "item_type", ""))
        collided[(item_type, _key(getattr(c, "item_name", "")))] = list(
            getattr(c, "conflicting_domains", []) or []
        )

    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []
    edge_ids: set = set()

    def add_node(node_id: str, **data: Any) -> None:
        if node_id not in nodes:
            nodes[node_id] = {"id": node_id, **data}

    def add_edge(source: str, target: str, kind: str, label: Optional[str] = None, **extra: Any) -> None:
        eid = f"{kind}:{source}->{target}:{label or ''}"
        if eid in edge_ids:
            return
        edge_ids.add(eid)
        edges.append({"id": eid, "source": source, "target": target, "kind": kind, "label": label, **extra})

    def bi_node(target: str) -> str:
        text = str(target).strip()
        kind = "column" if "." in text else "table_or_measure"
        node_id = f"bi:{_key(text)}"
        add_node(node_id, type="bi", label=text, bi_kind=kind)
        return node_id

    # Concept & metric lookup across all shown domains (relationships, metric.concept,
    # and metric.derives_from may point at an item another domain defines).
    concept_ids: Dict[str, str] = {}
    metric_ids: Dict[str, str] = {}
    domain_ids: Dict[str, str] = {}

    def _policies_for(dom: Any, item_name: str) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        target_key = _key(item_name)
        for pol in getattr(dom, "policies", None) or []:
            applies = [_key(x) for x in (getattr(pol, "applies_to", None) or [])]
            if not applies or "*" in applies or target_key in applies:
                out.append(
                    {
                        "name": pol.name,
                        "description": getattr(pol, "description", None),
                        "filter_expression": pol.filter_expression,
                    }
                )
        return out

    for dom in domains:
        did = str(getattr(dom, "id", None) or _key(dom.domain))
        dnode = f"domain:{did}"
        domain_ids[did] = dnode
        dom_policies = [
            {
                "name": p.name,
                "description": getattr(p, "description", None),
                "filter_expression": p.filter_expression,
                "applies_to": list(getattr(p, "applies_to", None) or []),
            }
            for p in (getattr(dom, "policies", None) or [])
        ]
        add_node(
            dnode,
            type="domain",
            label=dom.domain,
            owner=getattr(dom, "owner", None),
            version=getattr(dom, "version", None),
            description=getattr(dom, "description", None),
            source_project_id=getattr(dom, "source_project_id", None),
            concept_count=len(dom.concepts),
            metric_count=len(dom.metrics),
            policy_count=len(dom_policies),
            policies=dom_policies,
        )
        for c in dom.concepts:
            cnode = f"concept:{did}:{_key(c.name)}"
            concept_ids.setdefault(_key(c.name), cnode)
            for syn in c.synonyms or []:
                concept_ids.setdefault(_key(syn), cnode)
            attrs = [
                {
                    "name": a.name,
                    "maps_to": list(a.maps_to or []),
                    "synonyms": list(a.synonyms or []),
                    "description": a.description,
                    "is_hidden": bool(getattr(a, "is_hidden", False)),
                    "format_string": getattr(a, "format_string", None),
                    "stale": bool(getattr(a, "stale", False)),
                }
                for a in (c.attributes or [])
            ]
            add_node(
                cnode,
                type="concept",
                label=c.label or c.name,
                domain=dom.domain,
                description=c.description,
                synonyms=list(c.synonyms or []),
                business_rules=list(c.business_rules or []),
                filter_expression=getattr(c, "filter_expression", None),
                policies=_policies_for(dom, c.name),
                data_steward=c.data_steward,
                maps_to=list(c.maps_to or []),
                attributes=attrs,
                stale=bool(c.stale),
                unbound=not (c.maps_to or c.physical_table or c.source_dataset),
                collision=collided.get(("concept", _key(c.name))),
            )
            add_edge(dnode, cnode, "defines")
            for target in list(c.maps_to or []) or [t for t in (c.physical_table, c.source_dataset) if t][:1]:
                add_edge(cnode, bi_node(target), "binds", "maps to")
        for m in dom.metrics:
            mnode = f"metric:{did}:{_key(m.name)}"
            metric_ids.setdefault(_key(m.name), mnode)
            metric_ids.setdefault(_norm_metric_key(m.name), mnode)
            if getattr(m, "logical_name", None):
                metric_ids.setdefault(_key(m.logical_name), mnode)
                metric_ids.setdefault(_norm_metric_key(m.logical_name), mnode)
            if getattr(m, "label", None):
                metric_ids.setdefault(_key(m.label), mnode)
                metric_ids.setdefault(_norm_metric_key(m.label), mnode)
            for syn in m.synonyms or []:
                metric_ids.setdefault(_key(syn), mnode)
                metric_ids.setdefault(_norm_metric_key(syn), mnode)

    for dom in domains:
        did = str(getattr(dom, "id", None) or _key(dom.domain))
        dnode = domain_ids[did]
        for m in dom.metrics:
            mnode = f"metric:{did}:{_key(m.name)}"
            target_concept = concept_ids.get(_key(m.concept)) if m.concept else None
            derives_from = list(getattr(m, "derives_from", None) or [])
            missing_deps = [
                dep for dep in derives_from
                if not (metric_ids.get(_key(dep)) or metric_ids.get(_norm_metric_key(dep)))
            ]
            unresolved_msgs = []
            if m.concept and not target_concept:
                unresolved_msgs.append(f"concept '{m.concept}' is not defined")
            for dep in missing_deps:
                unresolved_msgs.append(f"upstream metric '{dep}' is not defined")
            add_node(
                mnode,
                type="metric",
                label=m.label or m.name,
                domain=dom.domain,
                description=m.description,
                synonyms=list(m.synonyms or []),
                business_rules=list(m.business_rules or []),
                filter_expression=getattr(m, "filter_expression", None),
                policies=_policies_for(dom, m.name),
                derives_from=derives_from,
                impacted_metrics=[],
                data_steward=m.data_steward,
                maps_to=list(m.maps_to or []),
                sql_expression=m.sql_expression,
                aggregation_type=m.aggregation_type,
                stale=bool(m.stale),
                unbound=not (m.maps_to or derives_from),
                unresolved="; ".join(unresolved_msgs) if unresolved_msgs else None,
                collision=collided.get(("metric", _key(m.name))),
            )
            add_edge(dnode, mnode, "defines")
            if target_concept:
                add_edge(mnode, target_concept, "measures", "measures")
            for target in m.maps_to or []:
                add_edge(mnode, bi_node(target), "binds", "maps to")

    # Second pass for metric-to-metric lineage edges (derives_from) and downstream impact tracking
    for dom in domains:
        did = str(getattr(dom, "id", None) or _key(dom.domain))
        for m in dom.metrics:
            mnode = f"metric:{did}:{_key(m.name)}"
            for dep in getattr(m, "derives_from", None) or []:
                base_mnode = metric_ids.get(_key(dep)) or metric_ids.get(_norm_metric_key(dep))
                if base_mnode and base_mnode in nodes:
                    add_edge(mnode, base_mnode, "derives", "derives from")
                    impacted = nodes[base_mnode].setdefault("impacted_metrics", [])
                    if (m.label or m.name) not in impacted:
                        impacted.append(m.label or m.name)

    for dom in domains:
        for r in dom.relationships:
            src = concept_ids.get(_key(r.from_concept))
            dst = concept_ids.get(_key(r.to_concept))
            card = getattr(r.cardinality, "value", r.cardinality)
            rel_type = getattr(getattr(r, "relationship_type", None), "value", getattr(r, "relationship_type", "JOIN"))
            default_label = str(card or "") if rel_type == "JOIN" else str(rel_type)
            label = r.predicate_label or default_label
            if src and dst:
                add_edge(src, dst, "relates", label, stale=bool(r.stale), relationship_type=rel_type)
            else:
                missing = [n for n, ok in ((r.from_concept, src), (r.to_concept, dst)) if not ok]
                for node_id in (src, dst):
                    if node_id:
                        nodes[node_id].setdefault("unresolved_relationships", []).append(
                            f"{r.from_concept} -> {r.to_concept}: {', '.join(missing)} not defined"
                        )

    for pid, ids in attachments.items():
        shown = [i for i in ids if i in domain_ids]
        if not shown:
            continue
        pnode = f"project:{pid}"
        add_node(pnode, type="project", label=project_names.get(pid) or pid, project_id=pid)
        for order, did in enumerate(ids, start=1):
            if did in domain_ids:
                add_edge(pnode, domain_ids[did], "uses", f"#{order}", order=order)

    node_list = list(nodes.values())
    summary = {
        "domains": sum(1 for n in node_list if n["type"] == "domain"),
        "concepts": sum(1 for n in node_list if n["type"] == "concept"),
        "metrics": sum(1 for n in node_list if n["type"] == "metric"),
        "bi_objects": sum(1 for n in node_list if n["type"] == "bi"),
        "projects": sum(1 for n in node_list if n["type"] == "project"),
        "relationships": sum(1 for e in edges if e["kind"] == "relates"),
        "metric_dependencies": sum(1 for e in edges if e["kind"] == "derives"),
        "policies": sum(n.get("policy_count", 0) for n in node_list if n["type"] == "domain"),
        "collisions": sum(1 for n in node_list if n.get("collision")),
        "stale": sum(1 for n in node_list if n.get("stale")),
        "unbound": sum(1 for n in node_list if n.get("unbound")),
        "unresolved": sum(1 for n in node_list if n.get("unresolved") or n.get("unresolved_relationships")),
    }
    return {"nodes": node_list, "edges": edges, "summary": summary}
