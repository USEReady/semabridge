"""Test Drift Detection on real PBIX semantic model extraction."""

from datetime import datetime, timezone
import pytest
from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
from semabridge.governance.drift.comparison import compare_views
from semabridge.governance.drift.models import (
    DriftCategory,
    ObjectDef,
    ViewDefinition,
)


@pytest.fixture
def pbix_extracted_objects():
    pbix_path = r"output\uploads\files\0d09bada55ae4eeab8851c6ca8564df5_Human Resources Sample PBIX.pbix"
    connector = LocalPBIXConnector({"pbix_path": pbix_path})
    connector.authenticate()
    discovered = connector.discover()

    tables = discovered.get("tables", [])
    measures = discovered.get("measures", [])

    objects = []
    # Tables / Facts
    for t in tables:
        t_name = str(t.get("name", "")).upper().replace(" ", "_")
        if t_name:
            objects.append(
                ObjectDef(
                    kind="FACT",
                    table=t_name,
                    name=t_name,
                    expression=f"SEMABRIDGE.ANALYTICS.{t_name}",
                )
            )

    # Measures / Metrics
    for m in measures:
        m_name = str(m.get("name", "")).upper().replace(" ", "_")
        dax = str(m.get("expression", "")).strip()
        t_name = str(m.get("table", "") or m.get("table_name", "") or "").upper()
        if m_name and dax:
            objects.append(
                ObjectDef(
                    kind="METRIC",
                    table=t_name,
                    name=m_name,
                    expression=dax,
                    description=f"Measure {m_name}",
                )
            )

    return objects


def test_pbix_identical_model_produces_zero_drift(pbix_extracted_objects):
    """PBIX extracted baseline compared with identical live view must produce 0 drift."""
    now = datetime.now(timezone.utc)
    base_view = ViewDefinition(
        view_name="HR_SEMANTIC_VIEW",
        platform="SNOWFLAKE",
        source="ddl",
        source_path="ddl",
        retrieved_at=now,
        objects=list(pbix_extracted_objects),
    )
    live_view = ViewDefinition(
        view_name="HR_SEMANTIC_VIEW",
        platform="SNOWFLAKE",
        source="ddl",
        source_path="ddl",
        retrieved_at=now,
        objects=list(pbix_extracted_objects),
    )

    findings = compare_views(base_view, live_view)
    assert len(findings) == 0, f"Expected 0 drift, got {len(findings)}"


def test_pbix_formula_modification_detected_cleanly(pbix_extracted_objects):
    """Modifying one measure formula in live view produces exactly one EXPRESSION_DRIFT."""
    now = datetime.now(timezone.utc)
    base_view = ViewDefinition(
        view_name="HR_SEMANTIC_VIEW",
        platform="SNOWFLAKE",
        source="ddl",
        source_path="ddl",
        retrieved_at=now,
        objects=list(pbix_extracted_objects),
    )

    modified_objects = []
    for o in pbix_extracted_objects:
        if o.name == "AVG_TENURE_DAYS":
            modified_objects.append(
                ObjectDef(
                    kind=o.kind,
                    table=o.table,
                    name=o.name,
                    expression="AVERAGE(Employee[TenureDays]) * 1.10",
                    description=o.description,
                )
            )
        else:
            modified_objects.append(o)

    live_view = ViewDefinition(
        view_name="HR_SEMANTIC_VIEW",
        platform="SNOWFLAKE",
        source="ddl",
        source_path="ddl",
        retrieved_at=now,
        objects=modified_objects,
    )

    findings = compare_views(base_view, live_view)
    assert len(findings) == 1
    assert findings[0].category == DriftCategory.EXPRESSION_DRIFT
    assert findings[0].name == "AVG_TENURE_DAYS"


def test_pbix_warehouse_addition_detected_cleanly(pbix_extracted_objects):
    """Adding a new metric to warehouse produces exactly one ADDITION."""
    now = datetime.now(timezone.utc)
    base_view = ViewDefinition(
        view_name="HR_SEMANTIC_VIEW",
        platform="SNOWFLAKE",
        source="ddl",
        source_path="ddl",
        retrieved_at=now,
        objects=list(pbix_extracted_objects),
    )

    added_objects = list(pbix_extracted_objects) + [
        ObjectDef(
            kind="METRIC",
            table="EMPLOYEE",
            name="Q4_ATTRITION_INDEX",
            expression="SUM(EMPLOYEE.SEPS) / COUNT(EMPLOYEE.ACTIVES)",
            description="Q4 attrition KPI",
        )
    ]

    live_view = ViewDefinition(
        view_name="HR_SEMANTIC_VIEW",
        platform="SNOWFLAKE",
        source="ddl",
        source_path="ddl",
        retrieved_at=now,
        objects=added_objects,
    )

    findings = compare_views(base_view, live_view)
    assert len(findings) == 1
    assert findings[0].category == DriftCategory.ADDITION
    assert findings[0].name == "Q4_ATTRITION_INDEX"


def test_pbix_warehouse_deletion_detected_cleanly(pbix_extracted_objects):
    """Dropping a metric from warehouse produces exactly one DELETION."""
    now = datetime.now(timezone.utc)
    base_view = ViewDefinition(
        view_name="HR_SEMANTIC_VIEW",
        platform="SNOWFLAKE",
        source="ddl",
        source_path="ddl",
        retrieved_at=now,
        objects=list(pbix_extracted_objects),
    )

    deleted_objects = [o for o in pbix_extracted_objects if o.name != "ACTIVES"]

    live_view = ViewDefinition(
        view_name="HR_SEMANTIC_VIEW",
        platform="SNOWFLAKE",
        source="ddl",
        source_path="ddl",
        retrieved_at=now,
        objects=deleted_objects,
    )

    findings = compare_views(base_view, live_view)
    assert len(findings) == 1
    assert findings[0].category == DriftCategory.DELETION
    assert findings[0].name == "ACTIVES"
