
import sqlite3
import pytest
from datetime import datetime
from semabridge.core.sentinel.persistence import FailureStore
from semabridge.core.sentinel.monitor import QueryFailure

@pytest.fixture
def failure_store(tmp_path):
    # Use a temporary file for the database
    db_path = tmp_path / "test_sentinel.db"
    return FailureStore(str(db_path))

def test_add_and_get_failure(failure_store):
    failure = QueryFailure(
        query_id="sc-12345",
        query_text="SELECT * FROM foo",
        database_name="TEST_DB",
        schema_name="TEST_SCHEMA",
        error_code=904,
        error_message="Invalid identifier",
        start_time=datetime.now()
    )
    
    # Test Add
    added = failure_store.add_failure(failure)
    assert added is True
    
    # Test Add Duplicate
    added_again = failure_store.add_failure(failure)
    assert added_again is False

    # Test Get
    retrieved = failure_store.get_failure("sc-12345")
    assert retrieved is not None
    assert retrieved.query_id == "sc-12345"
    assert retrieved.error_code == 904
    # Note: start_time might be string in sqlite, check usage if strict

def test_failure_status(failure_store):
    failure = QueryFailure(
        query_id="sc-status-test",
        query_text="SELECT 1",
        database_name=None, schema_name=None,
        error_code=1, error_message="Error",
        start_time=datetime.now()
    )
    failure_store.add_failure(failure)
    
    # Initial status
    status = failure_store.get_failure_status("sc-status-test")
    assert status["status"] == "DETECTED"
    assert status["fix_query"] is None
    
    # Update status
    failure_store.update_status("sc-status-test", "FIXED", "SELECT 1;")
    
    new_status = failure_store.get_failure_status("sc-status-test")
    assert new_status["status"] == "FIXED"
    assert new_status["fix_query"] == "SELECT 1;"

def test_get_non_existent(failure_store):
    assert failure_store.get_failure("non-existent") is None
    assert failure_store.get_failure_status("non-existent") is None
