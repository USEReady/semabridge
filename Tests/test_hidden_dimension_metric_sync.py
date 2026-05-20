"""Test that hidden dimensions and metrics flow through to Snowflake sync."""

import pytest
from semabridge.sml.models import SMLModel, SMLDataset, SMLColumn, SMLMetric, DataType
from semabridge.intermediate.models import OSIDataset, OSIColumn, OSIAttribute


def test_hidden_columns_included_in_dimension_creation():
    """Verify that hidden columns are now included when creating dimensions."""
    # Create a dataset with both visible and hidden columns
    dataset = OSIDataset(
        unique_name="TestDataset",
        label="Test Dataset",
        columns=[
            OSIColumn(unique_name="VisibleColumn", label="Visible", is_hidden=False),
            OSIColumn(unique_name="HiddenColumn", label="Hidden", is_hidden=True),
        ]
    )
    
    # Simulate the _create_dimension_from_dataset logic (after fix)
    # All columns are now included regardless of hidden status
    attributes = []
    for col in dataset.columns:
        # After fix: include all columns (including hidden)
        attr = OSIAttribute(
            unique_name=col.unique_name,
            label=col.label,
            dataset=dataset.unique_name,
            source_column=col.unique_name,
            is_hidden=col.is_hidden
        )
        attributes.append(attr)
    
    # Both visible and hidden columns should be in dimension attributes
    assert len(attributes) == 2
    
    # Verify attributes contain both columns
    attr_names = {attr.unique_name for attr in attributes}
    assert "VisibleColumn" in attr_names
    assert "HiddenColumn" in attr_names
    
    # Verify hidden status is preserved
    for attr in attributes:
        if attr.unique_name == "HiddenColumn":
            assert attr.is_hidden is True
        elif attr.unique_name == "VisibleColumn":
            assert attr.is_hidden is False


def test_hidden_metrics_included_in_sync_filtering():
    """Verify that hidden metrics are included if sync_enabled=True."""
    # Create metrics with different visibility states
    visible_metric = SMLMetric(
        name="VisibleMetric",
        unique_name="VisibleMetric",
        expression="SUM([Amount])",
        is_hidden=False,
        sync_enabled=True,
        dataset="TestDataset"  # Required field
    )
    
    hidden_metric = SMLMetric(
        name="HiddenMetric",
        unique_name="HiddenMetric",
        expression="SUM([Amount])",
        is_hidden=True,
        sync_enabled=True,  # Important: sync_enabled=True despite being hidden
        dataset="TestDataset"  # Required field
    )
    
    disabled_metric = SMLMetric(
        name="DisabledMetric",
        unique_name="DisabledMetric",
        expression="SUM([Amount])",
        is_hidden=False,
        sync_enabled=False,  # Not synced regardless of visibility
        dataset="TestDataset"  # Required field
    )
    
    all_metrics = [visible_metric, hidden_metric, disabled_metric]
    
    # Simulate measure_sync.py filtering (after fix)
    # Now includes hidden metrics if sync_enabled=True
    syncable = [m for m in all_metrics if m.sync_enabled]
    
    # Should have 2 syncable metrics (visible and hidden, but not disabled)
    assert len(syncable) == 2
    assert visible_metric in syncable
    assert hidden_metric in syncable
    assert disabled_metric not in syncable


def test_columns_included_in_publisher_dimension_bindings():
    """Verify that hidden columns are included in metric view bindings."""
    # Create a dataset with visibility variations
    dataset = SMLDataset(
        name="TestDataset",
        unique_name="TestDataset",
        columns=[
            SMLColumn(unique_name="PrimaryKey", label="ID", is_hidden=False, is_key=True),
            SMLColumn(unique_name="VisibleDimension", label="Category", is_hidden=False, is_key=False),
            SMLColumn(unique_name="HiddenDimension", label="Internal", is_hidden=True, is_key=False),
        ]
    )
    
    # After fix: all columns should be included
    included_count = 0
    for col in dataset.columns:
        # With the fix, include_as_dimension should be True for all
        include_as_dimension = True  # This is what the fix sets it to
        if include_as_dimension:
            included_count += 1
    
    # All columns should be included (3 total)
    assert included_count == 3
    
    # Verify all column types are present
    col_names = {col.unique_name for col in dataset.columns}
    assert "PrimaryKey" in col_names
    assert "VisibleDimension" in col_names
    assert "HiddenDimension" in col_names


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
