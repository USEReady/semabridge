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

from typing import Any, Dict, Iterable, List, Optional, Tuple


def _key(text: Any) -> str:
    return str(text or "").strip().casefold()


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
        keep_ids = set(attachments.get(str(project_id), []))
        domains = [d for d in domains if getattr(d, "id", None) in keep_ids]
        attachments = {str(project_id): attachments.get(str(project_id), [])}

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

    # Concept lookup across all shown domains (relationships and metric.concept
    # may point at a concept another domain defines).
    concept_ids: Dict[str, str] = {}
    domain_ids: Dict[str, str] = {}

    for dom in domains:
        did = str(getattr(dom, "id", None) or _key(dom.domain))
        dnode = f"domain:{did}"
        domain_ids[did] = dnode
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

    for dom in domains:
        did = str(getattr(dom, "id", None) or _key(dom.domain))
        dnode = domain_ids[did]
        for m in dom.metrics:
            mnode = f"metric:{did}:{_key(m.name)}"
            target_concept = concept_ids.get(_key(m.concept)) if m.concept else None
            add_node(
                mnode,
                type="metric",
                label=m.label or m.name,
                domain=dom.domain,
                description=m.description,
                synonyms=list(m.synonyms or []),
                business_rules=list(m.business_rules or []),
                data_steward=m.data_steward,
                maps_to=list(m.maps_to or []),
                sql_expression=m.sql_expression,
                aggregation_type=m.aggregation_type,
                stale=bool(m.stale),
                unbound=not m.maps_to,
                unresolved=(f"concept '{m.concept}' is not defined" if m.concept and not target_concept else None),
                collision=collided.get(("metric", _key(m.name))),
            )
            add_edge(dnode, mnode, "defines")
            if target_concept:
                add_edge(mnode, target_concept, "measures", "measures")
            for target in m.maps_to or []:
                add_edge(mnode, bi_node(target), "binds", "maps to")
        for r in dom.relationships:
            src = concept_ids.get(_key(r.from_concept))
            dst = concept_ids.get(_key(r.to_concept))
            card = getattr(r.cardinality, "value", r.cardinality)
            label = r.predicate_label or str(card or "")
            if src and dst:
                add_edge(src, dst, "relates", label, stale=bool(r.stale))
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
        "collisions": sum(1 for n in node_list if n.get("collision")),
        "stale": sum(1 for n in node_list if n.get("stale")),
        "unbound": sum(1 for n in node_list if n.get("unbound")),
        "unresolved": sum(1 for n in node_list if n.get("unresolved") or n.get("unresolved_relationships")),
    }
    return {"nodes": node_list, "edges": edges, "summary": summary}
