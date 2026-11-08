"""End-to-end ontology tests: upload YAML -> attach to project -> convert a small
synthetic model -> generated Snowflake DDL carries WITH SYNONYMS / COMMENT.

Also covers validation (unknown keys, duplicates, undefined references, path
traversal), domain-name stability, rename-by-id, re-sync merge and precedence.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from semabridge.api.main import app
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.connectors.synonym_clause import (
    ddl_annotations_disabled,
    strip_synonyms_clauses,
    synonyms_clause,
)
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.settings import SnowflakeConfig
from semabridge.intermediate.models import OSIColumn, OSIDataset, OSIMetric, OSIModel
from semabridge.ontology.apply import apply_ontology_to_model, apply_project_ontology
from semabridge.ontology.federated_loader import FederatedOntologyLoader as L
from semabridge.ontology.federated_loader import OntologyValidationError
from semabridge.ontology.store import DomainOntologyStore
from semabridge.sml.models import (
    AggregationType,
    DataType,
    SMLColumn,
    SMLDataset,
    SMLMetric,
    SMLModel,
)

import os

from semabridge.auth.tokens import create_access_token

os.environ.setdefault("JWT_SECRET_KEY", "pytest-only-secret")

# Send a real signed token so the tests pass whether or not AUTH_ENABLED is set.
client = TestClient(app, headers={"Authorization": "Bearer " + create_access_token({"sub": "1", "role": "admin"})})

FINANCE_YAML = """
domain: Finance Ops
concepts:
  - name: Sales
    synonyms: [Orders, "O'Brien's book"]
    description: Sales fact governed by finance
    business_rules: [Posted orders only]
    attributes:
      - name: Region
        synonyms: [Territory]
        description: 'Sales region \\ area'
metrics:
  - name: Revenue
    maps_to: [Total Sales]
    synonyms: [Turnover]
    description: Recognised revenue
    business_rules: [Excludes returns]
    data_steward: CFO Office
  - name: Ghost Metric
    synonyms: [Phantom]
"""


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the API singleton at an isolated, empty storage dir."""
    from semabridge.ontology import store as store_mod

    s = DomainOntologyStore(storage_dir=tmp_path / "ontologies")
    monkeypatch.setattr(store_mod, "ontology_store", s)
    import semabridge.api.routers.domain_ontology_router as router_mod

    monkeypatch.setattr(router_mod, "ontology_store", s)
    return s


def _sml() -> SMLModel:
    return SMLModel(
        unique_name="Retail",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                source_table="Sales",
                is_fact=True,
                description="BI sales table",
                columns=[
                    SMLColumn(unique_name="Amount", data_type=DataType.DECIMAL),
                    SMLColumn(unique_name="Region", data_type=DataType.STRING, description="BI region"),
                ],
            )
        ],
        metrics=[
            SMLMetric(
                unique_name="Total Sales",
                dataset="Sales",
                sql_expression='SUM(SALES."AMOUNT")',
                aggregation=AggregationType.SUM,
                description="BI total",
                synonyms=["Gross"],
            )
        ],
    )


def _ddl(sml: SMLModel) -> str:
    cfg = SnowflakeConfig(
        account="a", user="u", password="p", warehouse="w", database="DB", schema_name="SC", role="r"
    )
    return "\n".join(SnowflakeEmitter(cfg, ConnectorBehavior()).generate_ddls(sml))


def _line(ddl: str, needle: str) -> str:
    return next(line for line in ddl.splitlines() if needle in line)


# --------------------------------------------------------------------- e2e


def test_upload_attach_convert_emits_synonyms_and_comments(store):
    up = client.post(
        "/api/ontology/domains/upload",
        files=[("files", ("anything.yaml", FINANCE_YAML.encode(), "application/x-yaml"))],
    )
    assert up.status_code == 200, up.text
    assert up.json()["uploaded_domains"] == ["Finance Ops"]  # YAML 'domain' wins over filename

    att = client.put("/api/ontology/projects/p-1/domains", json={"domains": ["Finance Ops"]})
    assert att.status_code == 200, att.text
    assert att.json()["domains"] == [{"id": "finance_ops", "domain": "Finance Ops"}]

    sml = _sml()
    report = apply_project_ontology("p-1", sml, store=store, overrides={})
    assert any("Ghost Metric" in u for u in report["unmatched"])
    assert report["applied"] == {"tables": 1, "columns": 1, "metrics": 1}

    ddl = _ddl(sml)
    table = _line(ddl, 'SALES AS "DB"."SC"."SALES"')
    assert "WITH SYNONYMS = ('Orders', 'O''Brien''s book')" in table
    assert "COMMENT = 'Sales fact governed by finance. Business rules: Posted orders only.'" in table
    assert table.index("WITH SYNONYMS") < table.index("COMMENT")

    region = _line(ddl, 'SALES."REGION" AS')
    assert "WITH SYNONYMS = ('Territory')" in region
    assert "COMMENT = 'Sales region \\\\ area'" in region  # backslash escaped for Snowflake

    metric = _line(ddl, '"TOTAL_SALES" AS')
    # Synonyms follow the expression and precede COMMENT.
    assert metric.index(" AS ") < metric.index("WITH SYNONYMS") < metric.index("COMMENT")
    assert "'Turnover'" in metric and "'Gross'" in metric
    assert "Recognised revenue. Business rules: Excludes returns. Data steward: CFO Office." in metric


def test_precedence_user_override_beats_domain_beats_bi(store):
    dom = L.parse_yaml_string(FINANCE_YAML)
    sml = _sml()
    overrides = {
        ("retail", "sales", "total sales"): {"description": "User wording", "synonyms": ["Rev"], "business_rules": [], "data_steward": None},
        ("retail", "sales", "amount"): {"description": "", "synonyms": ["Value"], "business_rules": [], "data_steward": None},
    }
    apply_ontology_to_model(sml, domains=[dom], overrides=overrides)
    m = sml.metrics[0]
    assert m.description.startswith("User wording.")  # user > domain > BI
    assert "Business rules: Excludes returns." in m.description  # domain rules still apply
    assert m.synonyms[:3] == ["Rev", "Turnover", "Gross"]  # user, domain, BI order
    amount = sml.datasets[0].get_column("Amount")
    assert amount.synonyms == ["Value"] and amount.description == ""  # no domain/BI text: stays empty
    region = sml.datasets[0].get_column("Region")
    assert region.description == "Sales region \\ area"  # domain beats BI


def test_apply_is_deterministic_and_idempotent():
    dom = L.parse_yaml_string(FINANCE_YAML)
    a, b = _sml(), _sml()
    apply_ontology_to_model(a, domains=[dom])
    apply_ontology_to_model(b, domains=[dom])
    apply_ontology_to_model(b, domains=[dom])
    assert a.model_dump(exclude={"created_at"}) == b.model_dump(exclude={"created_at"})


def test_synonyms_flag_can_be_switched_off():
    assert synonyms_clause(["a"]) == " WITH SYNONYMS = ('a')"
    with ddl_annotations_disabled():
        assert synonyms_clause(["a"], "desc") == " COMMENT = 'desc'"
    assert strip_synonyms_clauses("x AS y WITH SYNONYMS = ('a(b)', 'c''d') COMMENT = 'k'") == "x AS y COMMENT = 'k'"


# --------------------------------------------------------------- validation


def test_unknown_key_rejected_with_key_name(store):
    with pytest.raises(OntologyValidationError) as exc:
        L.parse_yaml_string("domain: X\nconcepts:\n  - name: A\n    synonymz: [foo]\n")
    assert "Unknown key 'synonymz'" in str(exc.value)

    resp = client.post("/api/ontology/domains", json={"yaml_content": "domain: X\nbogus: 1\n"})
    assert resp.status_code == 422
    assert "Unknown key 'bogus'" in resp.json()["detail"]["errors"][0]


def test_duplicates_and_undefined_references_rejected():
    with pytest.raises(OntologyValidationError, match="Duplicate metric name"):
        L.parse_yaml_string("domain: X\nmetrics:\n  - name: M\n  - name: m\n")
    with pytest.raises(OntologyValidationError, match="undefined concept 'Missing'"):
        L.parse_yaml_string("domain: X\nmetrics:\n  - name: m\n    concept: Missing\n")
    with pytest.raises(OntologyValidationError, match="Duplicate key 'domain'"):
        L.parse_yaml_string("domain: X\ndomain: Y\n")
    with pytest.raises(OntologyValidationError, match="Missing required key 'domain'"):
        L.parse_yaml_string("concepts: []\n")


def test_upload_all_failing_returns_422_with_per_file_errors(store):
    resp = client.post(
        "/api/ontology/domains/upload",
        files=[
            ("files", ("a.yaml", b"concepts: []\n", "application/x-yaml")),
            ("files", ("b.yaml", b"domain: B\nrelationships:\n  - {from_concept: Nope, to_concept: Gone}\n", "application/x-yaml")),
            ("files", ("c.txt", b"domain: C\n", "text/plain")),
        ],
    )
    assert resp.status_code == 422
    body = resp.json()["detail"]
    files = {e["file"] for e in body["file_errors"]}
    assert files == {"a.yaml", "b.yaml", "c.txt"}
    assert store.domains == {}


def test_upload_size_limit(store, monkeypatch):
    monkeypatch.setenv("SEMABRIDGE_ONTOLOGY_MAX_UPLOAD_BYTES", "20")
    resp = client.post(
        "/api/ontology/domains/upload",
        files=[("files", ("big.yaml", b"domain: Big\ndescription: " + b"x" * 100, "application/x-yaml"))],
    )
    assert resp.status_code == 422
    assert "upload limit" in resp.json()["detail"]["errors"][0]


def test_path_traversal_rejected(tmp_path):
    s = DomainOntologyStore(storage_dir=tmp_path / "store")
    for bad in ("../escaped", "a/b", "..\\x"):
        with pytest.raises(OntologyValidationError):
            s.add_or_update_domain(L.parse_yaml_string(f"domain: '{bad}'\n"))
    with pytest.raises(OntologyValidationError):
        L.parse_yaml_string("domain: Ok\nid: ../../etc\n")
    assert list(tmp_path.glob("*.yaml")) == []
    assert not any(tmp_path.rglob("escaped*"))


def test_domain_name_survives_restart_and_load_errors_surface(store, tmp_path):
    store.add_or_update_domain(L.parse_yaml_string("domain: ACME Finance-EU v1.2\n"))
    (store.storage_dir / "broken.yaml").write_text("domain: B\nfoo: 1\n")
    reloaded = DomainOntologyStore(storage_dir=store.storage_dir)
    assert list(reloaded.domains) == ["ACME Finance-EU v1.2"]
    assert reloaded.load_errors and "Unknown key 'foo'" in reloaded.load_errors[0]["error"]

    import semabridge.api.routers.domain_ontology_router as router_mod

    router_mod.ontology_store = reloaded
    try:
        body = client.get("/api/ontology/domains").json()
    finally:
        router_mod.ontology_store = store
    assert body["load_errors"][0]["file"] == "broken.yaml"


def test_rename_via_put_updates_in_place_and_keeps_attachment(store):
    client.post("/api/ontology/domains", json={"yaml_content": "domain: Sales\n"})
    client.put("/api/ontology/projects/p-2/domains", json={"domains": ["Sales"]})
    resp = client.put("/api/ontology/domains/Sales", json={"yaml_content": "domain: Revenue Ops\n"})
    assert resp.status_code == 200, resp.text
    assert list(store.domains) == ["Revenue Ops"]
    assert [p.name for p in store.storage_dir.glob("*.ontology.yaml")] == ["sales.ontology.yaml"]
    assert client.get("/api/ontology/projects/p-2/domains").json()["domains"] == [
        {"id": "sales", "domain": "Revenue Ops"}
    ]
    # Renaming onto another existing domain is rejected, not merged.
    client.post("/api/ontology/domains", json={"yaml_content": "domain: Other\n"})
    clash = client.put("/api/ontology/domains/Other", json={"yaml_content": "domain: Revenue Ops\n"})
    assert clash.status_code == 422


# ------------------------------------------------------------------ re-sync


def _osi(cols, metrics, ds_desc="BI v1"):
    return OSIModel(
        unique_name="M",
        label="M",
        datasets=[OSIDataset(unique_name="Orders", description=ds_desc, columns=[OSIColumn(unique_name=c) for c in cols])],
        metrics=[OSIMetric(unique_name=m, dataset="Orders", expression="SUM(x)") for m in metrics],
    )


def test_resync_keeps_user_items_and_marks_vanished_bi_items_stale():
    v1 = L.from_osi_model(_osi(["id", "amount", "legacy"], ["rev", "old_metric"]), domain_name="Sales")
    # User edits + user-added items.
    v1.concepts[0].description = "User text"
    v1.concepts[0].synonyms.append("Purchases")
    v1.metrics[0].business_rules = ["No test orders"]
    user = L.parse_yaml_string("domain: Sales\nconcepts:\n  - name: Customer\nmetrics:\n  - name: churn\n    sql_expression: COUNT(x)\n")
    v1.concepts.append(user.concepts[0])
    v1.metrics.append(user.metrics[0])

    report: dict = {}
    v2 = L.from_osi_model(_osi(["id", "amount", "new_col"], ["rev", "new_metric"], ds_desc="BI v2"),
                          domain_name="Sales", existing_ontology=v1, report=report)

    concepts = {c.name: c for c in v2.concepts}
    assert "Customer" in concepts and not concepts["Customer"].stale  # user-added kept
    orders = concepts["Orders"]
    assert orders.description == "User text"  # user edit kept over new BI text
    assert "Purchases" in orders.synonyms
    attrs = {a.name: a for a in orders.attributes}
    assert attrs["legacy"].stale and not attrs["new_col"].stale  # vanished -> stale, new added
    metrics = {m.name: m for m in v2.metrics}
    assert metrics["rev"].business_rules == ["No test orders"]
    assert metrics["old_metric"].stale
    assert "churn" in metrics and not metrics["churn"].stale
    assert "new_metric" in metrics
    assert "metric old_metric" in report["stale"] and "attribute Orders.legacy" in report["stale"]
    # Round-trips through YAML (stale flag persisted).
    again = L.parse_yaml_string(L.dump_domain_yaml(v2))
    assert {m.name for m in again.metrics if m.stale} == {"old_metric"}


def test_override_upsert_is_patch_style():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from semabridge.api.routers.synonyms_router import SynonymOverrideRequest, upsert_synonym_override
    from semabridge.repository.orm.models import SynonymOverride

    engine = create_engine("sqlite://")
    SynonymOverride.__table__.create(engine)
    db = sessionmaker(bind=engine)()

    def call(**fields):
        key = {"project_id": "p-patch", "model_name": "M", "table_name": "T", "column_name": "C"}
        return upsert_synonym_override(SynonymOverrideRequest(**key, **fields), db=db)

    call(synonyms=["a"], description="d", business_rules=["r"], data_steward="s")
    # Partial update: only description changes; synonyms / rules / steward untouched.
    part = call(description="new")
    assert part["synonyms"] == ["a"] and part["business_rules"] == ["r"] and part["data_steward"] == "s"
    assert part["description"] == "new"
    # Explicit empty values clear.
    cleared = call(description="", synonyms=[], business_rules=[])
    assert cleared["description"] is None and cleared["synonyms"] == [] and cleared["business_rules"] == []
    assert cleared["data_steward"] == "s"


def test_resync_refreshes_unedited_bi_description():
    v1 = L.from_osi_model(_osi(["id"], []), domain_name="Sales")
    v2 = L.from_osi_model(_osi(["id"], [], ds_desc="BI v2"), domain_name="Sales", existing_ontology=v1)
    assert v2.concepts[0].description == "BI v2"
