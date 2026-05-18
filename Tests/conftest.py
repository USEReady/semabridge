"""
Pytest fixtures for semabridge tests.

Provides reusable test data and mock objects for unit testing.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Load .env so SEMABRIDGE_DATABASE_URL (PostgreSQL) is visible to pytest
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env", override=False)
except ImportError:
    pass  # python-dotenv not installed; rely on shell environment

# Add src directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from semabridge.formats.sml.models import (
    SMLModel, SMLDataset, SMLColumn, SMLMetric, SMLRelationship,
    DataType, AggregationType, Cardinality
)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the ``--db-url`` CLI option.

    Allows running the test suite against any supported database dialect::

        pytest tests/ --db-url postgresql://user:pass@localhost/testdb
        pytest tests/ --db-url duckdb:///./test.db
        pytest tests/                     # defaults to SQLite in-memory
    """
    try:
        parser.addoption(
            "--db-url",
            action="store",
            default=None,
            help=(
                "SQLAlchemy connection URL for tests. "
                "Defaults to sqlite:///file::memory:?cache=shared&uri=true"
            ),
        )
    except ValueError:
        pass  # already registered by another conftest

# -----------------------------------------------------------------------------
# Database environment — force in-memory SQLite for all tests
# -----------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="session")
def _set_test_database_env(request: pytest.FixtureRequest):
    """Override SEMABRIDGE_DATABASE_URL to in-memory SQLite for the test session.

    The URL can be overridden via ``pytest --db-url <url>`` to run the full
    suite against PostgreSQL, MySQL, or a persistent DuckDB file.
    """
    import os

    cli_url: str | None = request.config.getoption("--db-url", default=None)
    test_url = cli_url or "sqlite:///file::memory:?cache=shared&uri=true"

    _old_url = os.environ.get("SEMABRIDGE_DATABASE_URL")
    _old_backend = os.environ.get("SEMABRIDGE_DB_BACKEND")

    os.environ["SEMABRIDGE_DATABASE_URL"] = test_url
    os.environ["SEMABRIDGE_DB_BACKEND"] = "orm"

    # Clear cached engine and db_resolver so they pick up the new URL.
    try:
        from semabridge.repository.orm.session_factory import db_manager
        db_manager.reset()
    except Exception:
        pass
    try:
        from semabridge.repository.orm.session_factory import reset_engine
        reset_engine()
    except Exception:
        pass
    try:
        from semabridge.core.db_resolver import clear_db_config_cache
        clear_db_config_cache()
    except Exception:
        pass

    yield

    # Restore original values
    if _old_url is not None:
        os.environ["SEMABRIDGE_DATABASE_URL"] = _old_url
    else:
        os.environ.pop("SEMABRIDGE_DATABASE_URL", None)
    if _old_backend is not None:
        os.environ["SEMABRIDGE_DB_BACKEND"] = _old_backend
    else:
        os.environ.pop("SEMABRIDGE_DB_BACKEND", None)

    try:
        from semabridge.repository.orm.session_factory import db_manager
        db_manager.reset()
    except Exception:
        pass
    try:
        from semabridge.repository.orm.session_factory import reset_engine
        reset_engine()
    except Exception:
        pass
    try:
        from semabridge.core.db_resolver import clear_db_config_cache
        clear_db_config_cache()
    except Exception:
        pass




# Initialize database schema for all tests
@pytest.fixture(autouse=True, scope="session")
def _init_test_schema(request: pytest.FixtureRequest):
    """Create all ORM tables in the test database."""
    # Wait for _set_test_database_env to complete first
    request.getfixturevalue('_set_test_database_env')
    
    try:
        from semabridge.repository.orm.base import Base
        from semabridge.repository.orm.session_factory import db_manager
        
        engine = db_manager.get_engine()
        Base.metadata.create_all(bind=engine, checkfirst=True)
    except Exception as e:
        # Log but don't fail — tests may set up their own schema
        import logging
        logging.warning(f"Failed to create ORM schema: {e}")
    
    yield


# -----------------------------------------------------------------------------
# SML Model Fixtures
# -----------------------------------------------------------------------------

@pytest.fixture
def sample_column():
    """Create a sample SML column."""
    return SMLColumn(
        unique_name="REVENUE",
        label="Revenue",
        data_type=DataType.DECIMAL,
        description="Total revenue amount",
        is_key=False
    )


@pytest.fixture
def sample_dataset(sample_column):
    """Create a sample SML dataset with columns."""
    return SMLDataset(
        unique_name="FACT_SALES",
        label="Sales Fact Table",
        description="Contains sales transactions",
        source_table="FACT_SALES",
        is_fact=True,
        columns=[
            sample_column,
            SMLColumn(unique_name="CUSTOMER_ID", data_type=DataType.INTEGER, is_key=True),
            SMLColumn(unique_name="PRODUCT_ID", data_type=DataType.INTEGER),
            SMLColumn(unique_name="ORDER_DATE", data_type=DataType.DATE),
        ]
    )


@pytest.fixture
def sample_dimension_dataset():
    """Create a sample dimension dataset."""
    return SMLDataset(
        unique_name="DIM_CUSTOMER",
        label="Customer Dimension",
        is_fact=False,
        columns=[
            SMLColumn(unique_name="ID", data_type=DataType.INTEGER, is_key=True),
            SMLColumn(unique_name="NAME", data_type=DataType.STRING),
            SMLColumn(unique_name="CITY", data_type=DataType.STRING),
        ]
    )


@pytest.fixture
def sample_metric():
    """Create a sample SML metric."""
    return SMLMetric(
        unique_name="Total Revenue",
        label="Total Revenue",
        dataset="FACT_SALES",
        source_column="REVENUE",
        aggregation=AggregationType.SUM,
        description="Sum of all revenue"
    )


@pytest.fixture
def sample_relationship():
    """Create a sample SML relationship."""
    return SMLRelationship(
        unique_name="REL_FACT_SALES_CUSTOMER_ID__DIM_CUSTOMER_ID",
        from_dataset="FACT_SALES",
        from_columns=["CUSTOMER_ID"],
        to_dataset="DIM_CUSTOMER",
        to_columns=["ID"],
        cardinality=Cardinality.MANY_TO_ONE,
        is_active=True
    )


@pytest.fixture
def sample_sml_model(sample_dataset, sample_dimension_dataset, sample_metric, sample_relationship):
    """Create a complete sample SML model."""
    return SMLModel(
        unique_name="test_model",
        label="Test Semantic Model",
        description="A test model for unit testing",
        datasets=[sample_dataset, sample_dimension_dataset],
        metrics=[sample_metric],
        relationships=[sample_relationship]
    )


# -----------------------------------------------------------------------------
# Fabric/TMDL Fixtures
# -----------------------------------------------------------------------------

@pytest.fixture
def sample_tmdl_json():
    """Create a sample Fabric semantic-model JSON structure (model.bim-compatible)."""
    return {
        "model": {
            "name": "SalesModel",
            "description": "Sales analytics model",
            "tables": [
                {
                    "name": "Sales",
                    "columns": [
                        {"name": "Revenue", "dataType": "double"},
                        {"name": "Quantity", "dataType": "int64"},
                        {"name": "CustomerID", "dataType": "int64"},
                    ],
                    "measures": [
                        {
                            "name": "Total Revenue",
                            "expression": "SUM([Revenue])",
                            "description": "Sum of revenue"
                        }
                    ]
                },
                {
                    "name": "Customer",
                    "columns": [
                        {"name": "ID", "dataType": "int64"},
                        {"name": "Name", "dataType": "string"},
                    ]
                }
            ],
            "relationships": [
                {
                    "name": "REL_SALES_CUSTOMERID__CUSTOMER_ID",
                    "fromTable": "Sales",
                    "fromColumn": "CustomerID",
                    "toTable": "Customer",
                    "toColumn": "ID",
                    "cardinality": "ManyToOne",
                    "isActive": True
                }
            ]
        }
    }


@pytest.fixture
def sample_tmsl_json(sample_tmdl_json):
    """Backward-compatible alias for legacy test names."""
    return sample_tmdl_json


# -----------------------------------------------------------------------------
# DuckDB Fixtures
# -----------------------------------------------------------------------------

@pytest.fixture
def temp_db_path():
    """Create a temporary database file path.
    
    Note: We don't pre-create the file to avoid Windows file locking issues.
    DuckDB will create it on first connection.
    """
    import uuid
    import tempfile
    # Generate unique path without creating/opening the file
    temp_dir = tempfile.gettempdir()
    db_path = os.path.join(temp_dir, f"semabridge_test_{uuid.uuid4().hex}.db")
    yield db_path
    # Cleanup after test (may fail if still open, that's ok)
    try:
        # Give DuckDB time to release the file
        import time
        time.sleep(0.1)
        if os.path.exists(db_path):
            os.unlink(db_path)
        # Also try to remove WAL and other temp files
        for ext in ['.wal', '.tmp']:
            if os.path.exists(db_path + ext):
                os.unlink(db_path + ext)
    except OSError:
        pass  # File cleanup is best-effort


@pytest.fixture
def model_repository():
    """Create a ModelRepository backed by an in-memory SQLite database."""
    from semabridge.repository.model_repository import ModelRepository
    repo = ModelRepository(url_override="sqlite:///:memory:")
    yield repo


@pytest.fixture
def duckdb_manager(model_repository):
    """Backward-compat alias — yields a ModelRepository backed by in-memory SQLite."""
    yield model_repository


# -----------------------------------------------------------------------------
# DAX Translation Fixtures
# -----------------------------------------------------------------------------

@pytest.fixture
def dax_expressions():
    """Sample DAX expressions for testing translation."""
    return {
        # Tier 1: Direct aggregations
        "tier1_sum": "SUM([Revenue])",
        "tier1_avg": "AVERAGE([Quantity])",
        "tier1_count": "COUNT([OrderID])",
        "tier1_distinctcount": "DISTINCTCOUNT([CustomerID])",
        "tier1_min": "MIN([Price])",
        "tier1_max": "MAX([Price])",
        "tier1_with_table": "SUM('Sales'[Revenue])",
        
        # Tier 2: CALCULATE (partial support)
        "tier2_calculate": "CALCULATE(SUM([Revenue]), Product[Color] = \"Red\")",
        
        # Tier 3: Complex (no translation)
        "tier3_time_intel": "TOTALYTD(SUM([Revenue]), 'Date'[Date])",
        "tier3_iterator": "SUMX(Sales, Sales[Quantity] * Sales[Price])",
    }
