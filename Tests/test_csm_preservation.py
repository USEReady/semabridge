"""Test CSM preserves all OSI fields."""

import json
from pathlib import Path
from semabridge.intermediate.models import OSIModel
from semabridge.converter.osi_to_csm import OsiToCsmConverter
def test_csm_preserves_source_expressions():
    """Test that source_expressions are not lost."""
    
    # Load your OSI model
    try:
        with open("output/intermediate_artifacts/3_osi_model.json") as f:
            osi_dict = json.load(f)
    except FileNotFoundError:
        # Skip if test file doesn't exist yet
        return
    
    osi_model = OSIModel.model_validate(osi_dict)
    converter = OsiToCsmConverter()
    csm_model = converter.convert(osi_model)
    
    # Count columns with source_expressions
    osi_count = 0
    csm_count = 0
    
    for ds in osi_model.datasets:
        for col in ds.columns:
            if col.source_expression:
                osi_count += 1
    
    for ds in csm_model.datasets:
        for col in ds.columns:
            if col.source_expression:
                csm_count += 1
    
    print(f"OSI source_expressions: {osi_count}")
    print(f"CSM source_expressions: {csm_count}")
    
    assert osi_count == csm_count


def test_csm_preserves_default_aggregations():
    """Test that default_aggregations are not lost."""
    
    try:
        with open("output/intermediate_artifacts/3_osi_model.json") as f:
            osi_dict = json.load(f)
    except FileNotFoundError:
        # Skip if test file doesn't exist yet
        return
        
    osi_model = OSIModel.model_validate(osi_dict)
    converter = OsiToCsmConverter()
    csm_model = converter.convert(osi_model)
    
    osi_count = 0
    csm_count = 0
    
    for ds in osi_model.datasets:
        for col in ds.columns:
            if col.default_aggregation:
                osi_count += 1
    
    for ds in csm_model.datasets:
        for col in ds.columns:
            if col.default_aggregation:
                csm_count += 1
    
    print(f"OSI default_aggregations: {osi_count}")
    print(f"CSM default_aggregations: {csm_count}")
    
    assert osi_count == csm_count
