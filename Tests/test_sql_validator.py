"""Tests for generic SQL governance validator, rewriter, governed splits, and checks."""

import pytest
from unittest.mock import MagicMock

from semabridge.ai.sql_validator import (
    validate_and_rewrite_generated_sql,
    SQLValidationResult,
)
from semabridge.sml.models import (
    SMLModel,
    SMLDataset,
    SMLColumn,
    SMLDimension,
    SMLMetric,
    SMLRelationship,
    DataType,
    AggregationType,
)


def _build_test_model():
    """Build a mock SML model with datasets, columns, metrics, and relationships."""
    emp_dataset = SMLDataset(
        unique_name="Employee",
        name="Employee",
        columns=[
            SMLColumn(unique_name="EmplID", data_type=DataType.STRING, is_key=True),
            SMLColumn(unique_name="Age", data_type=DataType.INTEGER),
            SMLColumn(unique_name="TenureDays", data_type=DataType.INTEGER),
            SMLColumn(unique_name="TermDate", data_type=DataType.DATE),
            SMLColumn(unique_name="BU", data_type=DataType.STRING),
            SMLColumn(
                unique_name="IS_ACTIVES",
                data_type=DataType.BOOLEAN,
                is_calculated=True,
                is_split_dimension=True,
                expression='(EMPLOYEE."TERMDATE" IS NULL)',
            ),
        ],
        row_count=1000,
    )
    bu_dataset = SMLDataset(
        unique_name="BU",
        name="BU",
        columns=[
            SMLColumn(unique_name="BU", data_type=DataType.STRING, is_key=True),
            SMLColumn(unique_name="Region", data_type=DataType.STRING),
        ],
        row_count=10,
    )
    sales_dataset = SMLDataset(
        unique_name="Sales",
        name="Sales",
        columns=[
            SMLColumn(unique_name="OrderID", data_type=DataType.STRING, is_key=True),
            SMLColumn(unique_name="Amount", data_type=DataType.DECIMAL),
            SMLColumn(unique_name="CustomerID", data_type=DataType.STRING),
        ],
        row_count=5000,
    )

    metrics = [
        SMLMetric(
            unique_name="avg_age",
            label="AVG Age",
            dataset="Employee",
            aggregation=AggregationType.AVG,
            source_column="Age",
            sql_expression='ROUND(AVG(EMPLOYEE."AGE"::FLOAT), 0)',
        ),
        SMLMetric(
            unique_name="avg_tenure_days",
            label="AVG Tenure Days",
            dataset="Employee",
            aggregation=AggregationType.AVG,
            source_column="TenureDays",
            sql_expression='AVG(EMPLOYEE."TENUREDAYS")',
        ),
        SMLMetric(
            unique_name="total_sales",
            label="Total Sales",
            dataset="Sales",
            aggregation=AggregationType.SUM,
            source_column="Amount",
            sql_expression='SUM(SALES."AMOUNT")',
        ),
        SMLMetric(
            unique_name="snapshot_headcount",
            label="Snapshot Headcount",
            dataset="Employee",
            aggregation=AggregationType.COUNT,
            source_column="EmplID",
            sql_expression='COUNT(EMPLOYEE."EMPLID")',
            non_additive_dimension=("Date", "Date"),
        ),
        SMLMetric(
            unique_name="actives",
            label="Actives",
            dataset="Employee",
            expression="CALCULATE(COUNT([EmplID]), FILTER(Employee, ISBLANK(Employee[TermDate])))",
            filter_predicate='(EMPLOYEE."TERMDATE" IS NULL)',
        ),
    ]

    relationships = [
        SMLRelationship(
            unique_name="rel_emp_bu",
            from_dataset="Employee",
            from_columns=["BU"],
            to_dataset="BU",
            to_columns=["BU"],
        ),
        SMLRelationship(
            unique_name="rel_sales_cust",
            from_dataset="Sales",
            from_columns=["CustomerID"],
            to_dataset="Customer",
            to_columns=["CustomerID"],
        ),
        SMLRelationship(
            unique_name="rel_returns_cust",
            from_dataset="Returns",
            from_columns=["CustomerID"],
            to_dataset="Customer",
            to_columns=["CustomerID"],
        ),
    ]

    return SMLModel(
        unique_name="TestSemanticModel",
        datasets=[emp_dataset, bu_dataset, sales_dataset],
        metrics=metrics,
        relationships=relationships,
    )


def test_governed_rewrite_success():
    """Free-form query mapping all aggregations and groupings rewrites to SEMANTIC_VIEW."""
    model = _build_test_model()
    raw_sql = """
    WITH __employee AS (
      SELECT tenuredays, termdate, bu, age FROM SEMABRIDGE.HUMAN_RESOURCES.EMPLOYEE_ENRICHED
    ), __bu AS (
      SELECT bu FROM SEMABRIDGE.HUMAN_RESOURCES.BU
    )
    SELECT
      b.bu AS business_unit,
      CASE WHEN e.termdate IS NULL THEN 'Active' ELSE 'Separated' END AS employee_status,
      ROUND(AVG(CAST(e.age AS DOUBLE)), 0) AS avg_age,
      AVG(e.tenuredays) AS avg_tenure_days
    FROM __employee AS e
    LEFT OUTER JOIN __bu AS b ON e.bu = b.bu
    GROUP BY b.bu, CASE WHEN e.termdate IS NULL THEN 'Active' ELSE 'Separated' END
    """
    res = validate_and_rewrite_generated_sql(
        sql=raw_sql,
        prompt="Compare average age and average tenure in days for employees who separated versus those still active, by business unit.",
        dialect="snowflake",
        model_metadata=model,
        semantic_view_name="SEMABRIDGE.HUMAN_RESOURCES.HR_SEMANTIC",
    )
    assert res.allowed is True
    assert res.query_path == "rewritten to governed"
    assert res.rewritten_sql is not None
    assert "SEMANTIC_VIEW(" in res.rewritten_sql
    assert "HR_SEMANTIC" in res.rewritten_sql
    assert "avg_age" in res.rewritten_sql
    assert "avg_tenure_days" in res.rewritten_sql
    assert "bu.bu" in res.rewritten_sql
    assert res.is_certified_eligible is False  # rewritten queries are not certifiable
    assert res.checks_passed is True


def test_governed_rewrite_partial_unmapped_stays_free_form():
    """Free-form query with unmapped calculation stays free-form and lists unmapped items."""
    model = _build_test_model()
    raw_sql = """
    SELECT
      e.bu,
      AVG(e.tenuredays) AS avg_tenure_days,
      SUM(e.age * 2.5) AS unknown_custom_metric
    FROM Employee e
    GROUP BY e.bu
    """
    res = validate_and_rewrite_generated_sql(
        sql=raw_sql,
        prompt="Show average tenure and unknown metric by BU",
        dialect="snowflake",
        model_metadata=model,
        semantic_view_name="SEMABRIDGE.HUMAN_RESOURCES.HR_SEMANTIC",
    )
    assert res.query_path == "free-form (not governed)"
    assert res.is_certified_eligible is False
    assert len(res.unmapped_elements) > 0
    assert any("unknown_custom_metric" in u or "SUM" in u for u in res.unmapped_elements)


def test_already_governed_semantic_view():
    """Existing SEMANTIC_VIEW(...) query remains governed."""
    model = _build_test_model()
    sv_sql = """
    SELECT bu.bu, avg_age
    FROM SEMANTIC_VIEW(
        SEMABRIDGE.HUMAN_RESOURCES.HR_SEMANTIC
        METRICS avg_age
        DIMENSIONS bu.bu
    );
    """
    res = validate_and_rewrite_generated_sql(
        sql=sv_sql,
        prompt="Show average age by BU",
        dialect="snowflake",
        model_metadata=model,
        semantic_view_name="SEMABRIDGE.HUMAN_RESOURCES.HR_SEMANTIC",
    )
    assert res.allowed is True
    assert res.query_path == "semantic_view"
    assert res.is_certified_eligible is True
    assert res.checks_passed is True


def test_grain_safety_snapshot_without_period_filter():
    """Aggregating over a snapshot table without single-period filter flags grain safety warning."""
    model = _build_test_model()
    raw_sql = """
    SELECT COUNT(e.emplid) AS total_count
    FROM Employee e
    """
    res = validate_and_rewrite_generated_sql(
        sql=raw_sql,
        prompt="How many employees do we have?",
        dialect="snowflake",
        model_metadata=model,
    )
    grain_check = next((c for c in res.checks if c["check"] == "grain_safety"), None)
    assert grain_check is not None
    assert grain_check["passed"] is False
    assert "snapshot table" in grain_check["message"].lower()


def test_grain_safety_snapshot_with_period_filter_passes():
    """Aggregating over a snapshot table with period filter passes grain safety check."""
    model = _build_test_model()
    raw_sql = """
    SELECT COUNT(e.emplid) AS total_count
    FROM Employee e
    WHERE e.termdate = '2024-01-01'
    """
    res = validate_and_rewrite_generated_sql(
        sql=raw_sql,
        prompt="How many employees on 2024-01-01?",
        dialect="snowflake",
        model_metadata=model,
    )
    grain_check = next((c for c in res.checks if c["check"] == "grain_safety"), None)
    assert grain_check is not None
    assert grain_check["passed"] is True


def test_join_safety_non_unique_key_additive_refusal():
    """Joining on a non-unique key with additive metrics is refused to prevent double counting."""
    model = _build_test_model()
    key_uniqueness = {
        "CUSTOMER": {
            "columns": ["CustomerID"],
            "rows": 1000,
            "distinct": 500,
            "is_unique": False,
        }
    }
    raw_sql = """
    SELECT SUM(s.amount) AS total_sales
    FROM Sales s
    JOIN Customer c ON s.customerid = c.customerid
    """
    res = validate_and_rewrite_generated_sql(
        sql=raw_sql,
        prompt="Total sales by customer",
        dialect="snowflake",
        model_metadata=model,
        key_uniqueness_data=key_uniqueness,
    )
    assert res.allowed is False
    assert "possible double counting" in res.refusal_reason.lower()


def test_join_safety_fan_out_path_refusal():
    """Traversing a many->one->many fan-out join path with additive metrics is refused."""
    model = _build_test_model()
    raw_sql = """
    SELECT SUM(s.amount) AS total_sales
    FROM Sales s
    JOIN Returns r ON s.customerid = r.customerid
    """
    res = validate_and_rewrite_generated_sql(
        sql=raw_sql,
        prompt="Total sales and returns",
        dialect="snowflake",
        model_metadata=model,
    )
    assert res.allowed is False
    assert "double counting" in res.refusal_reason.lower()


def test_definition_check_condition_matching_model_rule():
    """Conditions in free-form SQL that align with governed metric definitions pass."""
    model = _build_test_model()
    raw_sql = """
    SELECT
      CASE WHEN e.termdate IS NULL THEN 'Active' ELSE 'Separated' END AS status,
      COUNT(e.emplid) AS count
    FROM Employee e
    GROUP BY CASE WHEN e.termdate IS NULL THEN 'Active' ELSE 'Separated' END
    """
    res = validate_and_rewrite_generated_sql(
        sql=raw_sql,
        prompt="Count of employees by status",
        dialect="snowflake",
        model_metadata=model,
    )
    def_check = next((c for c in res.checks if c["check"] == "definition_alignment"), None)
    assert def_check is not None
    assert def_check["passed"] is True


def test_definition_check_unverified_condition_becomes_assumption():
    """Conditions not backed by governed metric definitions surface as assumptions."""
    model = _build_test_model()
    raw_sql = """
    SELECT
      CASE WHEN e.age > 40 THEN 'Senior' ELSE 'Junior' END AS seniority,
      COUNT(e.emplid) AS count
    FROM Employee e
    GROUP BY CASE WHEN e.age > 40 THEN 'Senior' ELSE 'Junior' END
    """
    res = validate_and_rewrite_generated_sql(
        sql=raw_sql,
        prompt="Count by seniority",
        dialect="snowflake",
        model_metadata=model,
    )
    assert len(res.assumptions) > 0
    assert any("seniority" in a.lower() or "senior" in a.lower() or "age" in a.lower() for a in res.assumptions)


def test_governed_splits_sync_time():
    """CALCULATE(<agg>, <row-level predicate>) emits boolean dimension IS_<MEASURE> on the table."""
    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    from semabridge.intermediate.models import OSIModel, OSIDataset, OSIColumn, OSIMetric, OSIDataType

    osi = OSIModel(
        unique_name="TestModel",
        datasets=[
            OSIDataset(
                unique_name="Employee",
                columns=[
                    OSIColumn(unique_name="EmplID", data_type=OSIDataType.STRING, is_key=True),
                    OSIColumn(unique_name="TermDate", data_type=OSIDataType.DATE),
                ],
            )
        ],
        metrics=[
            OSIMetric(
                unique_name="Actives",
                dataset="Employee",
                expression="CALCULATE(COUNT([EmplID]), FILTER(Employee, ISBLANK(Employee[TermDate])))",
            ),
            OSIMetric(
                unique_name="Seps",
                dataset="Employee",
                expression="CALCULATE(COUNT([EmplID]), FILTER(Employee, NOT(ISBLANK(Employee[TermDate]))))",
            ),
        ],
    )

    converter = OSIToSMLConverter()
    sml = converter.convert(osi)

    emp_ds = next(d for d in sml.datasets if d.unique_name == "Employee")
    col_names = {c.unique_name for c in emp_ds.columns}
    assert "IS_ACTIVES" in col_names
    assert "IS_SEPS" in col_names

    is_actives_col = next(c for c in emp_ds.columns if c.unique_name == "IS_ACTIVES")
    assert is_actives_col.is_split_dimension is True
    assert "TERMDATE" in is_actives_col.expression
    assert "NULL" in is_actives_col.expression
