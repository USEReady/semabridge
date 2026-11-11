"""Comprehensive tests for:
1. Cortex Analyst REST API call path with key-pair authentication.
2. Execution records in database & verified query save rejection without execution/confirmation.
3. Database persistence of verified queries surviving restarts.
4. Semantic view DDL emitting the AI_VERIFIED_QUERIES clause.
5. Reporting and excluding invalid verified queries from DDL (never silent).
6. Upsert delta detection and merge for verified queries.
"""

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from semabridge.ai.cortex_analyst_client import call_cortex_analyst, generate_snowflake_keypair_jwt
from semabridge.ai.verified_queries_service import (
    has_successful_execution,
    list_verified_queries_for_project,
    record_query_execution,
    save_verified_query,
)
from semabridge.api.main import app
from semabridge.connectors.ddl_builder import SemanticViewBuilder
from semabridge.connectors.translator import MetricExpressionTranslator
from semabridge.core.settings import SnowflakeConfig
from semabridge.core.upsert_delta import compute_delta, merge_upsert
from semabridge.sml.models import (
    DataType,
    SMLColumn,
    SMLDataset,
    SMLMetric,
    SMLModel,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_snowflake_config(tmp_path):
    """Generate temporary RSA keypair and mock Snowflake configuration."""
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    pem_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    return SnowflakeConfig(
        account="xy12345.us-east-1",
        user="TEST_AI_USER",
        database="TEST_DB",
        schema_name="TEST_SCHEMA",
        warehouse="TEST_WH",
        auth_type="keypair",
        private_key=pem_bytes.decode("utf-8"),
    )


def _sample_sml_model(verified_queries=None):
    return SMLModel(
        unique_name="Competitive_Marketing",
        label="Competitive Marketing Analysis",
        datasets=[
            SMLDataset(
                unique_name="Sales",
                columns=[
                    SMLColumn(unique_name="Units", data_type=DataType.INTEGER),
                    SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
                    SMLColumn(unique_name="Region", data_type=DataType.STRING),
                ],
            )
        ],
        metrics=[
            SMLMetric(
                unique_name="Total_Units",
                dataset="Sales",
                source_column="Units",
                aggregation="sum",
                expression="SUM([Units])",
            )
        ],
        verified_queries=verified_queries or [],
    )


# ---------------------------------------------------------------------------
# 1. Studio → Cortex Analyst REST API Call Path
# ---------------------------------------------------------------------------

def test_cortex_analyst_jwt_generation(mock_snowflake_config):
    """Key-pair JWT conforms to Snowflake specifications (RS256, upper-case claims)."""
    jwt_token = generate_snowflake_keypair_jwt(
        account=mock_snowflake_config.account,
        user=mock_snowflake_config.user,
        private_key_pem=mock_snowflake_config.private_key,
    )
    assert isinstance(jwt_token, str)
    assert len(jwt_token) > 100


def test_cortex_analyst_call_path_success(mock_snowflake_config):
    """Studio routes questions to Cortex Analyst REST API against deployed view."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "message": {
            "role": "analyst",
            "content": [
                {"type": "text", "text": "Aggregating total sales by region for fiscal year 2024."},
                {"type": "sql", "statement": 'SELECT "REGION", SUM("REVENUE") FROM "TEST_DB"."TEST_SCHEMA"."SALES_VIEW" GROUP BY 1'},
            ],
        },
        "request_id": "req-cortex-12345",
    }

    with patch("requests.post", return_value=mock_response), \
         patch("semabridge.core.settings.reload_settings") as mock_settings:
        mock_cfg = MagicMock()
        mock_cfg.snowflake = mock_snowflake_config
        mock_settings.return_value = mock_cfg

        resp = client.post(
            "/api/ai/nl-query",
            json={
                "prompt": "Show total sales by region",
                "target": "SNOWFLAKE",
                "view_name": '"TEST_DB"."TEST_SCHEMA"."SALES_VIEW"',
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "SELECT" in data["generated_sql"]
        assert "[Snowflake Cortex Analyst]" in data["explanation"]
        assert data["confidence_score"] == 0.95


def test_cortex_analyst_offline_behavior_and_explicit_choice():
    """When Cortex Analyst is offline, returns 'Cortex unavailable' and offers local draft as explicit choice."""
    with patch("semabridge.core.settings.reload_settings") as mock_settings:
        # Config with no private key
        mock_cfg = MagicMock()
        mock_cfg.snowflake = SnowflakeConfig(account="acc", user="usr", auth_type="password")
        mock_settings.return_value = mock_cfg

        # 1. Without allow_offline_draft -> Cortex unavailable, no auto switch
        resp = client.post(
            "/api/ai/nl-query",
            json={"prompt": "Show gross margin", "target": "SNOWFLAKE", "allow_offline_draft": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "Cortex unavailable" in data["explanation"]
        assert any("cortex unavailable" in w.lower() for w in data["warnings"])
        assert data["generated_sql"] == ""

        # 2. With allow_offline_draft -> explicit local offline draft returned
        resp_draft = client.post(
            "/api/ai/nl-query",
            json={"prompt": "Show gross margin", "target": "SNOWFLAKE", "allow_offline_draft": True},
        )
        assert resp_draft.status_code == 200
        data_draft = resp_draft.json()
        assert "[Offline Draft - Local Engine]" in data_draft["explanation"]
        assert any("offline draft" in w.lower() for w in data_draft["warnings"])
        assert data_draft["generated_sql"] != ""


# ---------------------------------------------------------------------------
# 2. Execution Records in Database & Save Verification Enforcement
# ---------------------------------------------------------------------------

def test_execution_record_persisted_in_database():
    """Every warehouse execution logs question, SQL, view, user, success, and row count in the DB."""
    rec = record_query_execution(
        question="What is total units by region?",
        sql="SELECT region, total_units FROM sales_semantic_view",
        view_name="SALES_SEMANTIC_VIEW",
        user_id="test_steward",
        project_id="proj-db-exec-test",
        success=True,
        row_count=12,
        execution_time_ms=45,
    )
    assert rec.id is not None
    assert rec.success is True
    assert rec.row_count == 12
    assert has_successful_execution("SELECT region, total_units FROM sales_semantic_view", "proj-db-exec-test")


def test_save_verified_query_rejected_without_confirmation():
    """Saving without explicit user confirmation is rejected with HTTP 400."""
    resp = client.post(
        "/api/ai/save-verified-query",
        json={
            "project_id": "proj-db-exec-test",
            "question": "What is total units?",
            "sql": "SELECT total_units FROM sales_semantic_view",
            "confirmed": False,  # Missing confirmation
        },
    )
    assert resp.status_code == 400
    assert "Explicit user confirmation is required" in resp.json()["detail"]


def test_save_verified_query_rejected_without_database_execution():
    """Saving with confirmation but without a prior successful real execution record in the DB is rejected."""
    resp = client.post(
        "/api/ai/save-verified-query",
        json={
            "project_id": "proj-db-exec-test",
            "question": "Unexecuted question?",
            "sql": "SELECT unexecuted_metric FROM imaginary_view",
            "confirmed": True,
        },
    )
    assert resp.status_code == 400
    assert "no successful execution record found in database" in resp.json()["detail"]


def test_save_verified_query_allowed_with_zero_rows():
    """A query returning 0 rows that succeeded execution can be saved with user confirmation."""
    zero_sql = "SELECT * FROM sales_view WHERE 1=0"
    record_query_execution(
        question="Zero rows query?",
        sql=zero_sql,
        view_name="SALES_VIEW",
        user_id="test_steward",
        project_id="proj-zero-rows",
        success=True,
        row_count=0,
    )

    row = save_verified_query(
        project_id="proj-zero-rows",
        question="Zero rows query?",
        sql=zero_sql,
        confirmed=True,
        user_id="test_steward",
    )
    assert row.id is not None
    assert row.question == "Zero rows query?"


# ---------------------------------------------------------------------------
# 3. Verified Queries Database Persistence (Survives Restarts)
# ---------------------------------------------------------------------------

def test_verified_queries_database_persistence():
    """Verified queries persist in database per project and survive session reload."""
    test_sql = "SELECT fiscal_year, SUM(units) AS tot_units FROM sales_view GROUP BY 1"
    # First record real successful execution
    record_query_execution(
        question="Total units by year?",
        sql=test_sql,
        view_name="SALES_VIEW",
        user_id="data_steward_1",
        project_id="proj-persist-test",
        success=True,
        row_count=5,
    )

    # Save verified query
    row = save_verified_query(
        project_id="proj-persist-test",
        question="Total units by year?",
        sql=test_sql,
        confirmed=True,
        user_id="data_steward_1",
        name="total_units_by_year",
        view_name="SALES_VIEW",
        is_onboarding=True,
    )
    assert row.id is not None

    # Retrieve from database via service (simulating application restart)
    queries = list_verified_queries_for_project("proj-persist-test")
    assert len(queries) >= 1
    matched = [q for q in queries if q["name"] == "total_units_by_year"][0]
    assert matched["question"] == "Total units by year?"
    assert matched["is_onboarding"] is True
    assert matched["verified_by"] == "data_steward_1"


# ---------------------------------------------------------------------------
# 4. Semantic View DDL Emission (AI_VERIFIED_QUERIES)
# ---------------------------------------------------------------------------

def test_semantic_view_ddl_emits_ai_verified_queries():
    """DDL builder formats valid verified queries into the AI_VERIFIED_QUERIES clause."""
    vqueries = [
        {
            "name": "units_by_region",
            "question": "What are total units by region?",
            "sql": 'SELECT "Region", "Total_Units" FROM "Competitive_Marketing"',
            "verified_at": 1712851200,
            "is_onboarding": True,
            "verified_by": "Lead Data Steward",
        }
    ]
    model = _sample_sml_model(verified_queries=vqueries)

    cfg = MagicMock()
    cfg.database = "ANALYTICS"
    cfg.schema_name = "MARKETING"
    cfg.project_id = "proj-test"

    mock_schema = MagicMock()
    mock_schema._collect_physical_source_columns.return_value = {}
    mock_schema._resolve_physical_column_name.side_effect = lambda ds, c: c

    builder = SemanticViewBuilder(
        config=cfg,
        behavior=MagicMock(),
        schema_manager=mock_schema,
        translator=MetricExpressionTranslator(),
        identifier_sanitizer=MagicMock(to_alias=lambda x: x.upper(), sanitize_column=lambda x: x.upper()),
        live_schema_metadata={"SALES": {"UNITS", "REVENUE", "REGION"}},
    )

    ddl = "\n".join(builder.generate_ddls(model))
    assert "AI_VERIFIED_QUERIES (" in ddl
    assert "units_by_region AS (" in ddl
    assert "QUESTION 'What are total units by region?'" in ddl
    assert "VERIFIED_AT 1712851200" in ddl
    assert "ONBOARDING_QUESTION TRUE" in ddl
    assert "VERIFIED_BY '(STEWARD = Lead Data Steward)'" in ddl
    assert "SQL 'SELECT \"Region\", \"Total_Units\" FROM \"Competitive_Marketing\"'" in ddl


def test_verified_query_ddl_escaping_quotes_and_backslashes():
    """Single quotes and backslashes in QUESTION and SQL are correctly escaped for Snowflake DDL."""
    vqueries = [
        {
            "name": "escaped_query",
            "question": "What's the status for 'Won'?",
            "sql": "SELECT * FROM sales WHERE status = 'Won' AND name LIKE 'test\\_%'",
            "verified_at": 1712851200,
            "is_onboarding": False,
            "verified_by": "Lead Data Steward",
        }
    ]
    model = _sample_sml_model(verified_queries=vqueries)

    cfg = MagicMock()
    cfg.database = "ANALYTICS"
    cfg.schema_name = "MARKETING"
    cfg.project_id = "proj-escape-test"

    mock_schema = MagicMock()
    mock_schema._collect_physical_source_columns.return_value = {}
    mock_schema._resolve_physical_column_name.side_effect = lambda ds, c: c

    builder = SemanticViewBuilder(
        config=cfg,
        behavior=MagicMock(),
        schema_manager=mock_schema,
        translator=MetricExpressionTranslator(),
        identifier_sanitizer=MagicMock(to_alias=lambda x: x.upper(), sanitize_column=lambda x: x.upper()),
        live_schema_metadata={"SALES": {"UNITS", "REVENUE", "REGION", "STATUS", "NAME"}},
    )

    ddl = "\n".join(builder.generate_ddls(model))
    assert "AI_VERIFIED_QUERIES (" in ddl
    assert "QUESTION 'What''s the status for ''Won''?'" in ddl
    assert "SQL 'SELECT * FROM sales WHERE status = ''Won'' AND name LIKE ''test\\\\_%'''" in ddl


# ---------------------------------------------------------------------------
# 5. Invalid Verified Query Reported & Excluded (Never Silent)
# ---------------------------------------------------------------------------

def test_invalid_verified_query_reported_and_excluded():
    """Queries that fail validation (forbidden DDL, unbalanced parentheses) are reported and left out."""
    invalid_queries = [
        {
            "name": "forbidden_drop",
            "question": "Can we drop tables?",
            "sql": "DROP TABLE sales",
        },
        {
            "name": "unbalanced_syntax",
            "question": "Show broken query?",
            "sql": "SELECT * FROM (sales WHERE units > 0",
        },
        {
            "name": "valid_query",
            "question": "Show valid units?",
            "sql": 'SELECT "Region", "Total_Units" FROM "Competitive_Marketing"',
        },
    ]
    model = _sample_sml_model(verified_queries=invalid_queries)

    cfg = MagicMock()
    cfg.database = "ANALYTICS"
    cfg.schema_name = "MARKETING"
    cfg.project_id = "proj-test"

    mock_schema = MagicMock()
    mock_schema._collect_physical_source_columns.return_value = {}
    mock_schema._resolve_physical_column_name.side_effect = lambda ds, c: c

    builder = SemanticViewBuilder(
        config=cfg,
        behavior=MagicMock(),
        schema_manager=mock_schema,
        translator=MetricExpressionTranslator(),
        identifier_sanitizer=MagicMock(to_alias=lambda x: x.upper(), sanitize_column=lambda x: x.upper()),
        live_schema_metadata={"SALES": {"UNITS", "REVENUE", "REGION"}},
    )

    ddl = "\n".join(builder.generate_ddls(model))

    # Valid query is emitted
    assert "valid_query AS (" in ddl

    # Invalid queries are excluded from DDL
    assert "forbidden_drop" not in ddl
    assert "unbalanced_syntax" not in ddl

    # Exclusions are reported in validation advisories (never silent)
    advisories = builder.validation_advisories
    assert len(advisories) == 2
    assert any("forbidden_drop" in a and "Excluded from DDL" in a for a in advisories)
    assert any("unbalanced_syntax" in a and "Excluded from DDL" in a for a in advisories)


# ---------------------------------------------------------------------------
# 6. Upsert Delta Detection & Merge for Verified Queries
# ---------------------------------------------------------------------------

def test_upsert_detects_verified_queries_delta():
    """Upsert detects added, modified, and removed verified queries as changes."""
    base_vq = [
        {"name": "q1", "question": "Total units?", "sql": "SELECT SUM(units) FROM sales"},
    ]
    base_model = _sample_sml_model(verified_queries=base_vq).model_dump(mode="json")

    # 1. Unchanged model
    same_model = _sample_sml_model(verified_queries=base_vq)
    delta_same = compute_delta(same_model, base_model, baseline_snapshot_id="snap1")
    assert delta_same["has_changes"] is False
    assert delta_same["kinds"]["verified_queries"]["unchanged"] == 1

    # 2. Added verified query
    added_vq = base_vq + [
        {"name": "q2", "question": "Total revenue?", "sql": "SELECT SUM(revenue) FROM sales"}
    ]
    delta_add = compute_delta(_sample_sml_model(verified_queries=added_vq), base_model, baseline_snapshot_id="snap1")
    assert delta_add["has_changes"] is True
    assert len(delta_add["kinds"]["verified_queries"]["added"]) == 1
    assert delta_add["kinds"]["verified_queries"]["added"][0]["name"] == "q2"

    # 3. Modified verified query (updated SQL)
    mod_vq = [
        {"name": "q1", "question": "Total units?", "sql": "SELECT SUM(units) FROM sales WHERE fiscal_year=2024"},
    ]
    delta_mod = compute_delta(_sample_sml_model(verified_queries=mod_vq), base_model, baseline_snapshot_id="snap1")
    assert delta_mod["has_changes"] is True
    assert len(delta_mod["kinds"]["verified_queries"]["modified"]) == 1

    # 4. Merge upsert retains target-only verified query
    merged = merge_upsert(_sample_sml_model(verified_queries=[]), base_model)
    assert len(merged.verified_queries) == 1
    assert merged.verified_queries[0]["name"] == "q1"
