"""Connection Map graph: projects -> domains -> concepts / metrics -> BI objects."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from semabridge.auth.tokens import create_access_token
from semabridge.api.main import app
from semabridge.ontology.federated_loader import FederatedOntologyLoader as L
from semabridge.ontology.graph import build_ontology_graph
from semabridge.ontology.store import DomainOntologyStore

os.environ.setdefault("JWT_SECRET_KEY", "pytest-only-secret")
client = TestClient(app, headers={"Authorization": "Bearer " + create_access_token({"sub": "1", "role": "admin"})})

SALES = """
domain: Sales
concepts:
  - name: Order
    maps_to: [SalesFact]
    attributes:
      - name: Units
        maps_to: [SalesFact.Units]
metrics:
  - name: Total Units
    maps_to: [Total Units]
    concept: Order
"""

MARKET = """
domain: Market
concepts:
  - name: Product
    maps_to: [Product]
  - name: Sentiment
relationships:
  - from_concept: Order
    to_concept: Product
    predicate_label: sells
metrics:
  - name: Total Units
    concept: Product
    sql_expression: SUM(Units) * 1
"""


def _graph(project_id=None, attachments=None):
    sales, market = L.parse_yaml_string(SALES), L.parse_yaml_string(MARKET)
    sales.id, market.id = "sales", "market"
    fed = L.merge_domains([sales, market])
    return build_ontology_graph(
        [sales, market], attachments=attachments or {}, collisions=fed.collisions, project_id=project_id
    )


def _node(g, type_, label, domain=None):
    return next(
        n for n in g["nodes"]
        if n["type"] == type_ and n["label"] == label and (domain is None or n.get("domain") == domain)
    )


def _has_edge(g, kind, src, dst):
    return any(e["kind"] == kind and e["source"] == src and e["target"] == dst for e in g["edges"])


def test_connections_and_bindings():
    g = _graph(attachments={"p1": ["market", "sales"]})
    order = _node(g, "concept", "Order")
    product = _node(g, "concept", "Product")
    metric = _node(g, "metric", "Total Units", "Sales")
    assert _has_edge(g, "binds", order["id"], "bi:salesfact")
    assert _has_edge(g, "binds", metric["id"], "bi:total units")
    assert _has_edge(g, "measures", metric["id"], order["id"])
    # cross-domain relationship resolves to the concept another domain defines
    assert _has_edge(g, "relates", order["id"], product["id"])
    assert order["attributes"][0]["maps_to"] == ["SalesFact.Units"]
    # project attachments keep precedence order
    uses = sorted((e["order"], e["target"]) for e in g["edges"] if e["kind"] == "uses")
    assert uses == [(1, "domain:market"), (2, "domain:sales")]


def test_flags_collision_unbound_unresolved():
    g = _graph()
    for dom in ("Sales", "Market"):
        assert _node(g, "metric", "Total Units", dom)["collision"]
    assert _node(g, "concept", "Sentiment")["unbound"] is True
    assert _node(g, "concept", "Order")["unbound"] is False
    assert g["summary"]["collisions"] == 2
    assert g["summary"]["relationships"] == 1


def test_unresolved_relationship_is_reported_not_dropped():
    # Undefined relationship ends are rejected at upload; the builder still
    # reports them if a stored domain references a concept another domain removed.
    a = L.parse_yaml_string("domain: Solo\nconcepts:\n  - name: A\n")
    a.id = "solo"
    from semabridge.ontology.models import DomainRelationship

    a.relationships.append(DomainRelationship(from_concept="A", to_concept="Ghost"))
    g = build_ontology_graph([a])
    node = _node(g, "concept", "A")
    assert node["unresolved_relationships"] and "Ghost" in node["unresolved_relationships"][0]
    assert g["summary"]["unresolved"] == 1


def test_project_filter_limits_to_attached_domains():
    g = _graph(project_id="p1", attachments={"p1": ["sales"], "p2": ["market"]})
    assert {n["label"] for n in g["nodes"] if n["type"] == "domain"} == {"Sales"}
    assert {n["label"] for n in g["nodes"] if n["type"] == "project"} == {"p1"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    from semabridge.ontology import store as store_mod
    import semabridge.api.routers.domain_ontology_router as router_mod

    s = DomainOntologyStore(storage_dir=tmp_path / "ontologies")
    monkeypatch.setattr(store_mod, "ontology_store", s)
    monkeypatch.setattr(router_mod, "ontology_store", s)
    return s


def test_graph_endpoint_is_not_shadowed_by_domain_route(store):
    store.add_or_update_domain(L.parse_yaml_string(SALES), save_to_disk=True)
    store.set_project_domains("p1", ["Sales"])
    res = client.get("/api/ontology/domains/graph")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary"]["domains"] == 1 and body["summary"]["projects"] == 1
    res = client.get("/api/ontology/domains/graph", params={"project_id": "nobody"})
    assert res.status_code == 200 and res.json()["nodes"] == []
