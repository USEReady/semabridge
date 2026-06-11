import sys
import logging
sys.path.append('src')

try:
    from semabridge.intermediate.models import OSIModel, OSIDataset
    from semabridge.converter.osi_to_csm import OsiToCsmConverter
    
    print("Imports successful.")
    
    # Create dummy OSI model
    dataset = OSIDataset(unique_name="TestDataset", label="Test Dataset", source_table="sales", source_schema="dbo")
    model = OSIModel(unique_name="TestModel", label="Test Model", datasets=[dataset], version="1.0")
    
    # Run conversion
    converter = OsiToCsmConverter()
    csm_model = converter.convert(model)
    print("CSM conversion successful!")
    print(f"CSM Datasets: {list(csm_model.datasets.keys())}")
    
except Exception as e:
    print(f"CSM conversion failed: {e}")
