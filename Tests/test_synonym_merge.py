import pytest
from unittest.mock import patch, MagicMock
from semabridge.utils.synonyms import merge_synonyms, load_synonym_overrides
from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
from semabridge.intermediate.models import OSIDataType

def test_merge_synonyms_priority_layering():
    # Priority order:
    # 1. Custom overrides (explicitly provided)
    # 2. Source-defined synonyms (explicitly provided)
    # 3. Heuristic synonyms (explicitly provided)
    
    overrides = ["User Override 1", "User Override 2"]
    source = ["Source Synonym 1", "User Override 1", "Source Synonym 2"] # "User Override 1" is duplicate
    heuristics = ["Heuristic 1", "Source Synonym 1", "Heuristic 2"] # duplicates present

    # Test merge
    merged = merge_synonyms(overrides, source, heuristics, max_synonyms=5)
    
    # Check priority order and uniqueness
    assert merged == [
        "User Override 1",
        "User Override 2",
        "Source Synonym 1",
        "Source Synonym 2",
        "Heuristic 1"
    ]
    assert len(merged) == 5

def test_merge_synonyms_capping():
    overrides = ["A", "B", "C"]
    source = ["D", "E"]
    heuristics = ["F", "G"]
    
    # Cap at 4
    merged_4 = merge_synonyms(overrides, source, heuristics, max_synonyms=4)
    assert merged_4 == ["A", "B", "C", "D"]

    # Cap at 10 (larger than total)
    merged_all = merge_synonyms(overrides, source, heuristics, max_synonyms=10)
    assert merged_all == ["A", "B", "C", "D", "E", "F", "G"]

def test_tmsl_to_osi_integration_with_overrides():
    # Build a minimal raw TMSL structure
    tmsl = {
        "model": {
            "name": "TestModel",
            "tables": [
                {
                    "name": "Sales",
                    "columns": [
                        {
                            "name": "Revenue_Amount",
                            "dataType": "decimal",
                            "synonyms": ["Turnover", "Sales Amount"]
                        }
                    ],
                    "partitions": [
                        {
                            "name": "Partition",
                            "source": {
                                "type": "m",
                                "expression": "Source"
                            }
                        }
                    ],
                    "measures": [
                        {
                            "name": "Total Profit",
                            "expression": "SUM(Sales[Revenue_Amount])",
                            "synonyms": ["Profit"]
                        }
                    ]
                }
            ]
        }
    }

    # Custom overrides to inject
    overrides_cache = {
        "columns": {
            "Sales": {
                "Revenue_Amount": ["Top Line", "Turnover"] # Turnover is duplicate, Top Line is new
            }
        },
        "metrics": {
            "Total Profit": ["Bottom Line", "Net Earnings"] # New overrides
        }
    }

    # Prepare converter input
    source_data = {
        "tmsl": tmsl,
        "workspace_id": "test_ws",
        "dataset_id": "test_ds",
        "overrides_cache": overrides_cache
    }

    converter = TMSLToOSIConverter()
    osi_model = converter.to_osi(source_data)

    # 1. Verify Dataset Columns overrides and merging
    dataset = next((d for d in osi_model.datasets if d.unique_name == "Sales"), None)
    assert dataset is not None
    column = next((c for c in dataset.columns if c.unique_name == "Revenue_Amount"), None)
    assert column is not None

    # Expected column synonyms (Priority: Overrides -> Source -> Heuristics)
    # Overrides: "Top Line", "Turnover"
    # Source: "Turnover", "Sales Amount"
    # Heuristics (from label/name): "Revenue Amount" (etc)
    # The overrides must be first
    assert column.synonyms[0] == "Top Line"
    assert column.synonyms[1] == "Turnover"
    assert "Sales Amount" in column.synonyms
    assert "Revenue Amount" in column.synonyms

    # 2. Verify Metrics overrides and merging
    metric = next((m for m in osi_model.metrics if m.unique_name == "Total Profit"), None)
    assert metric is not None

    # Expected metric synonyms (Priority: Overrides -> Source -> Heuristics)
    # Overrides: "Bottom Line", "Net Earnings"
    # Source: "Profit"
    # Heuristics (from label/name): "Total Profit" (etc)
    assert metric.synonyms[0] == "Bottom Line"
    assert metric.synonyms[1] == "Net Earnings"
    assert "Profit" in metric.synonyms
    assert "Total Profit" in metric.synonyms
