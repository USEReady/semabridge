"""Targeted test suite verifying fixes for open Ontology & AI issues."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from semabridge.api.main import app
from semabridge.core.upsert_delta import compute_delta
from semabridge.governance.blast_radius import BlastRadiusSimulator, TargetType
from semabridge.governance.linter import SemanticLinter
from semabridge.intermediate.models import OSIAggregationType, OSIColumn, OSIDataset, OSIDataType, OSIMetric, OSIModel
from semabridge.ontology.federated_loader import FederatedOntologyLoader
from semabridge.ontology.models import (
    DomainAttribute,
    DomainConcept,
    DomainMetric,
    DomainOntology,
    DomainRelationship,
    FederatedEnterpriseOntology,
)
from semabridge.ontology.store import ontology_store
from semabridge.security.rls_sync import SecurityPolicyTranslator, TMDLSecurityRole, TMDLTablePermission
from semabridge.sml.models import DataType, SMLColumn, SMLDataset, SMLMetric, SMLModel

client = TestClient(app)


@pytest.fixture
def seeded_store(tmp_path):
    orig_domains, orig_dir = ontology_store.domains, ontology_store.storage_dir
    ontology_store.domains, ontology_store.storage_dir = {}, tmp_path
    try:
        yield ontology_store
    finally:
        ontology_store.domains, ontology_store.storage_dir = orig_domains, orig_dir
        ontology_store._refresh_index()


# -----------------------------------------------------------------------------
# 1. Studio Routing: Fully qualified semantic view name
# -----------------------------------------------------------------------------
def test_item1_studio_routing_fully_qualified():
    """Verify that routing qualifies view_name as DATABASE.SCHEMA.VIEW rather than bare name."""
    from unittest.mock import MagicMock, patch

    captured_views = []

    def mock_call_cortex(prompt, semantic_view, config):
        captured_views.append(semantic_view)
        return {"generated_sql": "SELECT 1;", "explanation": "test"}

    mock_settings = MagicMock()
    mock_settings.snowflake.account = "test_acct"
    mock_settings.snowflake.user = "test_user"
    mock_settings.snowflake.private_key = "dummy_private_key"
    mock_settings.snowflake.warehouse = "test_wh"
    mock_settings.snowflake.database = "TEST_DB"
    mock_settings.snowflake.schema_name = "TEST_SCHEMA"

    with patch("semabridge.core.settings.reload_settings", return_value=mock_settings), \
         patch("semabridge.ai.cortex_analyst_client.call_cortex_analyst", side_effect=mock_call_cortex):
        resp = client.post(
            "/api/ai/nl-query",
            json={
                "prompt": "Total sales",
                "dialect": "SNOWFLAKE",
                "target": "SNOWFLAKE",
                "view_name": "MY_BARE_VIEW",
            },
        )
        assert resp.status_code == 200
        assert len(captured_views) == 1
        fq_view = captured_views[0]
        # Must have 3 parts separated by dots: "DB"."SCHEMA"."VIEW"
        parts = [p.strip('"') for p in fq_view.split(".")]
        assert len(parts) == 3
        assert parts[0] == "TEST_DB"
        assert parts[1] == "TEST_SCHEMA"
        assert parts[2] == "MY_BARE_VIEW"


# -----------------------------------------------------------------------------
# 2. RLS Translator: Simple comparisons only, unsupported functions rejected, no default DB
# -----------------------------------------------------------------------------
def test_item2_rls_translator_unsupported_functions_and_no_default_db():
    translator = SecurityPolicyTranslator()

    # Unsupported function: USERPRINCIPALNAME()
    role_upn = TMDLSecurityRole(
        name="Dynamic_Manager",
        table_permissions=[
            TMDLTablePermission(table_name="Customer", dax_filter_expression="[Email] = USERPRINCIPALNAME()")
        ],
    )
    res_upn = translator.translate_tmdl_role(role_upn, target_database="TEST_DB", target_schema="ANALYTICS")
    assert res_upn["status"] == "not translated"
    assert "USERPRINCIPALNAME" in res_upn["reason"]
    assert res_upn["ddl"] == ""

    # Unsupported function: RELATED()
    role_related = TMDLSecurityRole(
        name="Geo_Manager",
        table_permissions=[
            TMDLTablePermission(table_name="Sales", dax_filter_expression="RELATED(Customer[Region]) = \"EMEA\"")
        ],
    )
    res_rel = translator.translate_tmdl_role(role_related, target_database="TEST_DB", target_schema="ANALYTICS")
    assert res_rel["status"] == "not translated"
    assert "RELATED" in res_rel["reason"]
    assert res_rel["ddl"] == ""

    # Missing database raises error (no default PROD_DWH)
    role_valid = TMDLSecurityRole(
        name="Simple_Role",
        table_permissions=[
            TMDLTablePermission(table_name="Customer", dax_filter_expression="[Region] = \"EMEA\"")
        ],
    )
    with pytest.raises(ValueError, match="target_database is required"):
        translator.tmdl_role_to_snowflake_ddl(role_valid, target_database="")


# -----------------------------------------------------------------------------
# 3. Blast Radius: No invented reports/tools, reject empty/invalid target, table_context, direct agg, skip stale
# -----------------------------------------------------------------------------
def test_item3_blast_radius_validation_and_scoping():
    # Reject empty target (400)
    resp_empty = client.post(
        "/api/governance/blast-radius",
        json={"target_name": "", "target_type": "COLUMN"},
    )
    assert resp_empty.status_code == 400

    # Reject invalid target type (400)
    resp_invalid = client.post(
        "/api/governance/blast-radius",
        json={"target_name": "col", "target_type": "INVALID_TYPE"},
    )
    assert resp_invalid.status_code == 400

    # Test simulator with OSI model and ontology with stale metric
    osi = OSIModel(
        unique_name="TestModel",
        datasets=[
            OSIDataset(
                unique_name="Sales",
                source_table="FACT_SALES",
                columns=[OSIColumn(unique_name="Units", data_type=OSIDataType.INTEGER, is_key=False)],
            ),
            OSIDataset(
                unique_name="Returns",
                source_table="FACT_RETURNS",
                columns=[OSIColumn(unique_name="Units", data_type=OSIDataType.INTEGER, is_key=False)],
            ),
        ],
        metrics=[
            # Direct aggregation on Sales.Units
            OSIMetric(unique_name="Total_Units", dataset="Sales", source_column="Units", aggregation=OSIAggregationType.SUM),
            # Direct aggregation on Returns.Units
            OSIMetric(unique_name="Return_Units", dataset="Returns", source_column="Units", aggregation=OSIAggregationType.SUM),
        ],
    )
    # Stale metric defined in ontology
    fed = FederatedEnterpriseOntology(
        metrics={
            "Stale_Sales_Units": DomainMetric(
                name="Stale_Sales_Units",
                sql_expression="SUM(Units)",
                concept="Sales",
                stale=True,
            )
        }
    )

    sim = BlastRadiusSimulator(osi_model=osi, ontology=fed)

    # Scoped to Sales table_context
    rep = sim.simulate_impact(target_name="Units", target_type=TargetType.COLUMN, table_context="Sales")
    # Must only match Sales.Total_Units, NOT Returns.Units, and skip Stale_Sales_Units
    affected = [m.name for m in rep.affected_metrics]
    assert "Total_Units" in affected
    assert "Return_Units" not in affected
    assert "Stale_Sales_Units" not in affected

    # No invented reports or AI tools
    assert rep.affected_bi_reports == []
    assert rep.affected_ai_tools == []


# -----------------------------------------------------------------------------
# 4. Upsert: Description and synonym changes count as metadata changed and are deployed
# -----------------------------------------------------------------------------
def test_item4_upsert_metadata_changes_detected():
    baseline_sml = {
        "metrics": [
            {
                "unique_name": "Revenue",
                "dataset": "Sales",
                "expression": "SUM(Sales[Amount])",
                "aggregation": "sum",
                "description": "Old revenue description",
                "synonyms": ["Sales"],
            }
        ]
    }
    new_model = SMLModel(
        unique_name="Test",
        datasets=[SMLDataset(unique_name="Sales", columns=[SMLColumn(unique_name="Amount", data_type=DataType.DECIMAL)])],
        metrics=[
            SMLMetric(
                unique_name="Revenue",
                dataset="Sales",
                expression="SUM(Sales[Amount])",
                description="Updated revenue description with new governance notes",
                synonyms=["Sales", "Topline"],
            )
        ],
        relationships=[],
    )

    delta = compute_delta(new_model, baseline_sml)
    assert delta["has_changes"] is True
    modified = delta["kinds"]["metrics"]["modified"]
    assert len(modified) == 1
    assert "description" in modified[0]["changed_fields"]
    assert "synonyms" in modified[0]["changed_fields"]
    assert modified[0]["change_category"] == "metadata changed"


# -----------------------------------------------------------------------------
# 5. Collisions: Different formulas are blockers with resolution choices; identical stay warnings
# -----------------------------------------------------------------------------
def test_item5_collisions_different_formulas_are_blockers():
    d1 = DomainOntology(
        domain="Finance",
        owner="fin@corp.com",
        metrics=[
            DomainMetric(name="ebitda", sql_expression="SUM(rev) - SUM(cogs)")
        ],
    )
    # Different formula -> ERROR blocker with namespace/priority choices
    d2 = DomainOntology(
        domain="Ops",
        owner="ops@corp.com",
        metrics=[
            DomainMetric(name="ebitda", sql_expression="SUM(operating_income) + SUM(depr)")
        ],
    )
    fed = FederatedOntologyLoader.merge_domains([d1, d2])
    assert len(fed.collisions) == 1
    col = fed.collisions[0]
    assert col.severity == "ERROR"
    assert col.is_blocker is True
    assert "namespace" in col.resolution_choices
    assert "priority" in col.resolution_choices

    # Identical formula -> WARNING (non-blocker)
    d3 = DomainOntology(
        domain="Ops2",
        owner="ops@corp.com",
        metrics=[
            DomainMetric(name="ebitda", sql_expression="SUM(rev) - SUM(cogs)")
        ],
    )
    fed_identical = FederatedOntologyLoader.merge_domains([d1, d3])
    assert len(fed_identical.collisions) == 1
    col_ident = fed_identical.collisions[0]
    assert col_ident.severity == "WARNING"
    assert col_ident.is_blocker is False


# -----------------------------------------------------------------------------
# 6. Ontology Metrics from PBIX: sql_expression is read-only; editing is rejected
# -----------------------------------------------------------------------------
def test_item6_pbix_metrics_sql_expression_read_only(seeded_store):
    # Create domain with a BI-originated metric
    dom_yaml = """
domain: SalesBI
owner: bi@corp.com
metrics:
  - name: total_sales
    sql_expression: "SUM(sales_amount)"
    origin: bi
    maps_to: ["Sales.TotalSales"]
"""
    resp_init = client.post("/api/ontology/domains", json={"yaml_content": dom_yaml})
    assert resp_init.status_code == 200

    # Attempt to edit sql_expression of this BI metric
    tampered_yaml = """
domain: SalesBI
owner: bi@corp.com
metrics:
  - name: total_sales
    sql_expression: "SUM(different_column)"
    origin: bi
    maps_to: ["Sales.TotalSales"]
"""
    resp_tamper = client.put(
        "/api/ontology/domains/SalesBI",
        json={"yaml_content": tampered_yaml},
    )
    assert resp_tamper.status_code == 422
    err_text = str(resp_tamper.json())
    assert "read-only" in err_text
    assert "total_sales" in err_text


# -----------------------------------------------------------------------------
# 7. Domain attributes keep is_hidden from OSI
# -----------------------------------------------------------------------------
def test_item7_domain_attributes_keep_is_hidden():
    osi = OSIModel(
        unique_name="TestHiddenModel",
        datasets=[
            OSIDataset(
                unique_name="Customers",
                source_table="DIM_CUSTOMERS",
                columns=[
                    OSIColumn(unique_name="ID", data_type=OSIDataType.INTEGER, is_key=True, is_hidden=False),
                    OSIColumn(unique_name="InternalSSN", data_type=OSIDataType.STRING, is_key=False, is_hidden=True),
                ],
            )
        ],
        metrics=[],
    )

    domain = FederatedOntologyLoader.from_osi_model(osi, domain_name="CustomerDomain")
    concept = domain.concepts[0]
    attrs = {a.name: a for a in concept.attributes}
    assert attrs["ID"].is_hidden is False
    assert attrs["InternalSSN"].is_hidden is True

    # Round-trip to OSI preserves is_hidden
    fed = FederatedOntologyLoader.merge_domains([domain])
    roundtrip_osi = FederatedOntologyLoader.to_osi_model(fed)
    rt_cols = {c.unique_name: c for c in roundtrip_osi.datasets[0].columns}
    assert rt_cols["ID"].is_hidden is False
    assert rt_cols["InternalSSN"].is_hidden is True


# -----------------------------------------------------------------------------
# 8. Linter: snake_case = ^[a-z0-9_]+$; report all cycles; check descriptions & stewards
# -----------------------------------------------------------------------------
def test_item8_linter_snake_case_all_cycles_and_stewards():
    fed = FederatedEnterpriseOntology(
        metrics={
            "UPPERCASE_METRIC": DomainMetric(
                name="UPPERCASE_METRIC",
                sql_expression="SUM(amount)",
                description="Valid description here",
                data_steward="steward@corp.com",
            )
        },
        concepts={
            "Customer": DomainConcept(
                name="Customer",
                description="Too", # < 5 characters
                data_steward=None, # Missing steward
                attributes=[
                    DomainAttribute(name="col1", description="") # Missing description
                ],
            ),
            "A": DomainConcept(name="A", description="Concept A", data_steward="s@c.com"),
            "B": DomainConcept(name="B", description="Concept B", data_steward="s@c.com"),
            "C": DomainConcept(name="C", description="Concept C", data_steward="s@c.com"),
            "D": DomainConcept(name="D", description="Concept D", data_steward="s@c.com"),
        },
        relationships=[
            # Cycle 1: A -> B -> A
            DomainRelationship(from_concept="A", to_concept="B"),
            DomainRelationship(from_concept="B", to_concept="A"),
            # Cycle 2: C -> D -> C
            DomainRelationship(from_concept="C", to_concept="D"),
            DomainRelationship(from_concept="D", to_concept="C"),
        ],
    )

    linter = SemanticLinter()
    report = linter.lint_ontology(fed)

    # 1. snake_case check
    naming_issues = [i for i in report.issues if i.rule_id == "RULE_NAMING_CONVENTION"]
    assert any("UPPERCASE_METRIC" in i.object_name for i in naming_issues)

    # 2. Concept & Attribute stewards and descriptions
    steward_issues = [i for i in report.issues if i.rule_id == "RULE_MISSING_STEWARD"]
    assert any(i.object_type == "CONCEPT" and i.object_name == "Customer" for i in steward_issues)

    desc_issues = [i for i in report.issues if i.rule_id == "RULE_MISSING_DESCRIPTION"]
    assert any(i.object_type == "CONCEPT" and i.object_name == "Customer" for i in desc_issues)
    assert any(i.object_type == "ATTRIBUTE" and "Customer.col1" in i.object_name for i in desc_issues)

    # 3. Report ALL cycles (must detect both Cycle 1 and Cycle 2)
    cycle_issues = [i for i in report.issues if i.rule_id == "RULE_CIRCULAR_JOIN"]
    assert len(cycle_issues) >= 2


# -----------------------------------------------------------------------------
# 9. Export of unknown domain -> 404
# -----------------------------------------------------------------------------
def test_item9_export_unknown_domain_404():
    resp = client.get("/api/ontology/domains/nonexistent_domain_xyz/export")
    assert resp.status_code == 404

    resp_yaml = client.get("/api/ontology/domains/export/yaml/nonexistent_domain_xyz")
    assert resp_yaml.status_code == 404


# -----------------------------------------------------------------------------
# 10. Verified by session user; no hardcoded fallbacks
# -----------------------------------------------------------------------------
def test_item10_verified_by_session_user_no_fallbacks():
    from unittest.mock import patch
    from semabridge.ai.verified_queries_service import save_verified_query

    # Save verified query with specific user
    with patch("semabridge.ai.verified_queries_service.has_successful_execution", return_value=True):
        row = save_verified_query(
            project_id="test_proj",
            question="What is profit?",
            sql="SELECT 1;",
            confirmed=True,
            user_id="alice@enterprise.com",
            name="test_profit_query",
        )
        assert row.verified_by == "alice@enterprise.com"
        assert row.verified_by != "Data Steward"
