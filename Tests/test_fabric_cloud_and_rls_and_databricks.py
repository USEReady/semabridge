"""Tests for Fabric Cloud Publisher, Bidirectional RLS Sync, and Databricks Unity Catalog Metric Emitter."""

import pytest
from fastapi.testclient import TestClient

from semabridge.api.main import app
from semabridge.connectors.databricks_metric_emitter import DatabricksMetricEmitter
from semabridge.connectors.fabric_cloud_deployer import FabricCloudDeployer, FabricDeploymentConfig
from semabridge.intermediate.models import OSIDataset, OSIMetric, OSIModel
from semabridge.security.rls_sync import (
    SecurityPolicyTranslator,
    SnowflakeRowAccessPolicy,
    TMDLSecurityRole,
    TMDLTablePermission,
)

client = TestClient(app)


def _sample_model():
    return OSIModel(
        unique_name="SALES_MODEL",
        label="Sales Model",
        datasets=[
            OSIDataset(unique_name="Orders", source_table="FACT_ORDERS", columns=[])
        ],
        metrics=[
            OSIMetric(
                unique_name="TotalRevenue",
                label="Total Revenue",
                dataset="Orders",
                expression="SUM(amount)",
                description="Total order revenue",
                business_owner="revops@corp.com",
            )
        ],
    )


# =============================================================================
# 1. Fabric Cloud Publisher Tests
# =============================================================================

def test_fabric_cloud_publisher_deployment():
    config = FabricDeploymentConfig(
        workspace_id="ws-999-alpha",
        dataset_name="EnterpriseSales",
        client_id="app-123",
        client_secret="secret-abc",
        tenant_id="tenant-xyz",
    )
    deployer = FabricCloudDeployer(config)
    model = _sample_model()

    receipt = deployer.deploy_model(model)
    assert receipt.success is True
    assert receipt.workspace_id == "ws-999-alpha"
    assert "EnterpriseSales" in receipt.dataset_name
    assert len(receipt.tmdl_files_deployed) > 0


def test_api_fabric_cloud_deploy():
    resp = client.post(
        "/api/fabric/deploy",
        json={"workspace_id": "ws-test-123", "dataset_name": "TestDataset"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["workspace_id"] == "ws-test-123"


# =============================================================================
# 2. Bidirectional RLS Security Tests
# =============================================================================

def test_tmdl_role_to_snowflake_ddl():
    translator = SecurityPolicyTranslator()
    role = TMDLSecurityRole(
        name="EMEA_Manager",
        table_permissions=[
            TMDLTablePermission(table_name="Customer", dax_filter_expression='[Region] = "EMEA"')
        ],
        members=["emea_group@enterprise.com"],
    )

    ddl = translator.tmdl_role_to_snowflake_ddl(role, "PROD_DWH", "ANALYTICS")
    assert "CREATE OR REPLACE ROW ACCESS POLICY" in ddl
    assert "RAP_CUSTOMER_EMEA_MANAGER" in ddl
    assert "ALTER TABLE PROD_DWH.ANALYTICS.CUSTOMER" in ddl
    assert "ADD ROW ACCESS POLICY PROD_DWH.ANALYTICS.RAP_CUSTOMER_EMEA_MANAGER" in ddl


def test_snowflake_policy_to_tmdl():
    translator = SecurityPolicyTranslator()
    policy = SnowflakeRowAccessPolicy(
        policy_name="RAP_CUSTOMER_APAC",
        target_table="Customer",
        column_name="REGION",
        body_expression="CURRENT_ROLE() = 'APAC_MANAGER' AND REGION = 'APAC'",
        assigned_roles=["APAC_MANAGER"],
    )

    tmdl = translator.snowflake_policy_to_tmdl(policy)
    assert "role 'APAC_MANAGER'" in tmdl
    assert "tablePermission 'Customer' = [REGION] = \"APAC\"" in tmdl


def test_api_security_rls_endpoints():
    role_payload = {
        "role": {
            "name": "US_Sales",
            "table_permissions": [
                {"table_name": "Sales", "dax_filter_expression": '[Country] = "US"'}
            ],
        },
        "target_database": "TEST_DWH",
    }
    resp = client.post("/api/security/rls/tmdl-to-snowflake", json=role_payload)
    assert resp.status_code == 200
    assert "CREATE OR REPLACE ROW ACCESS POLICY" in resp.json()["ddl"]


# =============================================================================
# 3. Databricks Unity Catalog Metric Emitter Tests
# =============================================================================

def test_databricks_unity_catalog_ddl_generation():
    emitter = DatabricksMetricEmitter(catalog="main_dwh", schema="curated_metrics")
    model = _sample_model()

    ddl = emitter.emit_unity_catalog_ddl(model)
    assert "CREATE OR REPLACE VIEW main_dwh.curated_metrics.sales_model_metric_view" in ddl
    assert "SUM(amount) AS totalrevenue" in ddl
    assert "ALTER VIEW main_dwh.curated_metrics.sales_model_metric_view ALTER COLUMN totalrevenue SET TAGS" in ddl


def test_databricks_genie_space_spec():
    emitter = DatabricksMetricEmitter(catalog="main_dwh", schema="curated_metrics")
    model = _sample_model()

    spec = emitter.emit_genie_space_spec(model)
    assert spec["version"] == "1.0"
    assert spec["catalog"] == "main_dwh"
    assert len(spec["measures"]) == 1
    assert spec["measures"][0]["metric_name"] == "TotalRevenue"


def test_api_databricks_metrics_endpoints():
    resp = client.post(
        "/api/databricks/metrics/emit-ddl",
        json={"model_name": "CorpSales", "catalog": "corp_lakehouse"},
    )
    assert resp.status_code == 200
    assert "CREATE OR REPLACE VIEW corp_lakehouse" in resp.json()["ddl"]

    genie_resp = client.post(
        "/api/databricks/metrics/emit-genie-space",
        json={"model_name": "CorpSales", "catalog": "corp_lakehouse"},
    )
    assert genie_resp.status_code == 200
    assert "measures" in genie_resp.json()
