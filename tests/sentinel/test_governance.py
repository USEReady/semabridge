
import pytest
from unittest.mock import MagicMock
from semabridge.core.sentinel.governance import DriftDetector, DriftReport
from semabridge.core.settings import SnowflakeConfig

@pytest.fixture
def detector():
    # Mock config
    config = MagicMock(spec=SnowflakeConfig)
    return DriftDetector(config)

def test_no_drift(detector):
    baseline = {
        "tables": {"T1": {}},
        "columns": {"T1": [{"name": "C1", "data_type": "VARCHAR"}]}
    }
    current = {
        "tables": {"T1": {}},
        "columns": {"T1": [{"name": "C1", "data_type": "VARCHAR"}]}
    }
    
    report = detector._compare(baseline, current)
    assert not report.has_drift
    assert not report.breaking_changes
    assert not report.non_breaking_changes

def test_table_dropped(detector):
    baseline = {"tables": {"T1": {}, "T2": {}}}
    current = {"tables": {"T1": {}}}
    
    report = detector._compare(baseline, current)
    assert report.has_drift
    assert report.is_breaking
    assert "Table dropped: T2" in report.breaking_changes

def test_table_added(detector):
    baseline = {"tables": {"T1": {}}}
    current = {"tables": {"T1": {}, "T2": {}}}
    
    report = detector._compare(baseline, current)
    assert report.has_drift
    assert not report.is_breaking
    assert "Table added: T2" in report.non_breaking_changes

def test_column_dropped(detector):
    baseline = {
        "tables": {"T1": {}},
        "columns": {"T1": [{"name": "C1", "data_type": "VARCHAR"}, {"name": "C2", "data_type": "INT"}]}
    }
    current = {
        "tables": {"T1": {}},
        "columns": {"T1": [{"name": "C1", "data_type": "VARCHAR"}]}
    }
    
    report = detector._compare(baseline, current)
    assert report.has_drift
    assert report.is_breaking
    assert "Column dropped in T1: C2" in report.breaking_changes

def test_column_type_change(detector):
    baseline = {
        "tables": {"T1": {}},
        "columns": {"T1": [{"name": "C1", "data_type": "VARCHAR"}]}
    }
    current = {
        "tables": {"T1": {}},
        "columns": {"T1": [{"name": "C1", "data_type": "INT"}]}
    }
    
    report = detector._compare(baseline, current)
    # Type change is considered breaking in current logic
    assert report.is_breaking
    assert any("Column type changed" in msg for msg in report.breaking_changes)
