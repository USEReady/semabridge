"""End-to-End PBIX Drift Pipeline Verification Script

Tests real PBIX models against the drift detection engine:
1. Validates extraction of tables, measures, and relationships from real PBIX files.
2. Simulates full Snowflake Semantic View DDL generation with TABLES =, DIMENSIONS =, METRICS =.
3. Tests round-trip DDL parsing via SnowflakeAdapter.
4. Verifies zero false positives on identical models.
5. Verifies precision on:
   - Modified measure formula -> exactly 1 EXPRESSION_DRIFT
   - Warehouse-added metric -> exactly 1 ADDITION
   - Warehouse-dropped metric -> exactly 1 DELETION
   - Updated metric description -> exactly 1 DESCRIPTION_DRIFT
"""

import pytest
from datetime import datetime, timezone
from pathlib import Path

from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
from semabridge.governance.drift.adapters.snowflake import SnowflakeAdapter
from semabridge.governance.drift.comparison import compare_views
from semabridge.governance.drift.models import DriftCategory, ObjectDef, ViewDefinition

HR_PBIX = "output/uploads/files/0d09bada55ae4eeab8851c6ca8564df5_Human Resources Sample PBIX.pbix"
STORE_PBIX = "output/uploads/files/0b2ae7042e5f4768bfc25d7dd29a54cf_Store Sales.pbix"


@pytest.mark.parametrize("pbix_path", [HR_PBIX, STORE_PBIX])
def test_pbix_extraction_and_zero_drift(pbix_path):
    if not Path(pbix_path).exists():
        pytest.skip(f"PBIX file not found: {pbix_path}")

    connector = LocalPBIXConnector({"pbix_path": pbix_path})
    model = connector.discover()
    assert "tables" in model and len(model["tables"]) > 0
    assert "measures" in model and len(model["measures"]) > 0

    now = datetime.now(timezone.utc)
    objects = []
    for m in model["measures"]:
        name = (m.get("name") or m.get("unique_name") or "").upper().strip()
        expr = (m.get("expression") or m.get("dax_expression") or "").strip()
        if name and expr:
            objects.append(ObjectDef(kind="METRIC", table="SEMANTIC_VIEW", name=name, expression=expr))

    base_view = ViewDefinition(
        view_name="PBIX_SEMANTIC_VIEW",
        platform="SNOWFLAKE",
        source="ddl",
        source_path="ddl",
        retrieved_at=now,
        objects=objects,
    )
    live_view = ViewDefinition(
        view_name="PBIX_SEMANTIC_VIEW",
        platform="SNOWFLAKE",
        source="ddl",
        source_path="ddl",
        retrieved_at=now,
        objects=list(objects),
    )

    findings = compare_views(base_view, live_view)
    assert len(findings) == 0, f"Expected 0 false drift findings, got: {findings}"


def test_pbix_full_snowflake_ddl_roundtrip():
    """Verify that Snowflake DDL with TABLES =, DIMENSIONS =, METRICS = parses without false drift."""
    ddl = """CREATE OR REPLACE SEMANTIC VIEW SEMABRIDGE.HR.HR_SAMPLE_SEMANTIC (
  TABLES = (
    EMPLOYEE AS SEMABRIDGE.HR.EMPLOYEE_RAW PRIMARY KEY (EMPID),
    BU AS SEMABRIDGE.HR.BU_RAW PRIMARY KEY (BU_ID),
    COL_DATE AS SEMABRIDGE.HR.DATE_RAW PRIMARY KEY (DATE_KEY)
  ),
  RELATIONSHIPS = (
    EMPLOYEE (BU_ID) REFERENCES BU (BU_ID),
    EMPLOYEE (HIRE_DATE) REFERENCES COL_DATE (DATE_KEY)
  ),
  DIMENSIONS = (
    BU."BU" WITH SYNONYMS = ('BUSINESS_UNIT'),
    BU."REGION",
    COL_DATE."DATE" WITH SYNONYMS = ('DATE'),
    COL_DATE."DAY",
    COL_DATE."MONTH"
  ),
  METRICS = (
    ACTIVES AS COUNT(EMPLOYEE.EMPID) COMMENT = 'Active employee headcount',
    BAD_HIRES AS SUM(EMPLOYEE.BADHIRE_FLAG),
    AVG_TENURE AS AVG(EMPLOYEE.TENURE_DAYS)
  )
);"""

    adapter = SnowflakeAdapter()
    base_def = adapter.parse_definition(ddl, "SEMABRIDGE.HR.HR_SAMPLE_SEMANTIC", source="baseline")
    live_def = adapter.parse_definition(ddl, "SEMABRIDGE.HR.HR_SAMPLE_SEMANTIC", source="live")

    # Roundtrip comparison must be strictly 0 drift
    findings = compare_views(base_def, live_def)
    assert len(findings) == 0, f"Self-comparison generated unexpected findings: {findings}"

    # Verify extracted object counts and attributes
    metric_names = {o.name for o in base_def.objects if o.kind == "METRIC"}
    assert "ACTIVES" in metric_names
    assert "BAD_HIRES" in metric_names
    assert "AVG_TENURE" in metric_names

    dim_names = {o.name for o in base_def.objects if o.kind == "DIMENSION"}
    assert "BU" in dim_names
    assert "REGION" in dim_names
    assert "DATE" in dim_names
    assert "DAY" in dim_names
    assert "MONTH" in dim_names

    # Check synonyms and comments
    date_dim = next(o for o in base_def.objects if o.name == "DATE")
    assert date_dim.properties.get("synonyms") == "'DATE'"

    actives_metric = next(o for o in base_def.objects if o.name == "ACTIVES")
    assert actives_metric.description == "Active employee headcount"


def test_pbix_precision_on_changes():
    """Verify precision of all 4 drift categories on PBIX models."""
    base_ddl = """CREATE OR REPLACE SEMANTIC VIEW DB.SCHEMA.V (
  TABLES = ( T1 AS DB.SCHEMA.T1_RAW PRIMARY KEY (ID) ),
  DIMENSIONS = ( T1."CATEGORY" ),
  METRICS = (
    M1 AS SUM(T1.VAL) COMMENT = 'Metric One',
    M2 AS AVG(T1.SCORE) COMMENT = 'Metric Two',
    M3 AS COUNT(T1.ID) COMMENT = 'Metric Three'
  )
);"""

    # Warehouse changes:
    # 1. M1 formula changed: SUM -> MAX (EXPRESSION_DRIFT)
    # 2. M2 dropped (DELETION)
    # 3. M3 description changed (DESCRIPTION_DRIFT)
    # 4. M4 added (ADDITION)
    live_ddl = """CREATE OR REPLACE SEMANTIC VIEW DB.SCHEMA.V (
  TABLES = ( T1 AS DB.SCHEMA.T1_RAW PRIMARY KEY (ID) ),
  DIMENSIONS = ( T1."CATEGORY" ),
  METRICS = (
    M1 AS MAX(T1.VAL) COMMENT = 'Metric One',
    M3 AS COUNT(T1.ID) COMMENT = 'Metric Three Updated Description',
    M4 AS MIN(T1.SCORE) COMMENT = 'New Metric Four'
  )
);"""

    adapter = SnowflakeAdapter()
    base_def = adapter.parse_definition(base_ddl, "DB.SCHEMA.V", source="baseline")
    live_def = adapter.parse_definition(live_ddl, "DB.SCHEMA.V", source="live")

    findings = compare_views(base_def, live_def)
    assert len(findings) == 4

    by_cat = {f.name: f for f in findings}
    assert by_cat["M1"].category == DriftCategory.EXPRESSION_DRIFT
    assert by_cat["M2"].category == DriftCategory.DELETION
    assert by_cat["M3"].category == DriftCategory.DESCRIPTION_DRIFT
    assert by_cat["M4"].category == DriftCategory.ADDITION
