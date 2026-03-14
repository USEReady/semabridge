
import sys
import os
import unittest
from datetime import datetime

# Add src to path
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

from semabridge.sml.models import (
    SMLModel, SMLDataset, SMLColumn, SMLMetric, SMLDimension, SMLAttribute,
    DataType, AggregationType, SourcePlatform
)
from semabridge.converter.sml_to_osi import SMLToOSIConverter
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.intermediate.models import OSIModel

class TestSMLOSIConversion(unittest.TestCase):
    def setUp(self):
        # Create a sample SML Model
        self.sml_model = SMLModel(
            unique_name="test_model",
            label="Test Model",
            description="A test model for conversion verification",
            source_platform=SourcePlatform.SNOWFLAKE,
            version="1.0"
        )
        
        # Add Dataset
        dataset = SMLDataset(
            unique_name="Sales",
            label="Sales Data",
            source_table="FACT_SALES",
            is_fact=True
        )
        dataset.columns.append(SMLColumn(
            unique_name="Amount",
            label="Amount",
            data_type=DataType.DECIMAL
        ))
        self.sml_model.datasets.append(dataset)
        
        # Add Metric
        self.sml_model.metrics.append(SMLMetric(
            unique_name="TotalAmount",
            label="Total Amount",
            dataset="Sales",
            source_column="Amount",
            aggregation=AggregationType.SUM
        ))

    def test_sml_to_osi_conversion(self):
        print("\nTesting SML -> OSI...")
        converter = SMLToOSIConverter()
        osi_model = converter.to_osi(self.sml_model)
        
        self.assertIsInstance(osi_model, OSIModel)
        self.assertEqual(osi_model.unique_name, "test_model")
        self.assertEqual(len(osi_model.datasets), 1)
        self.assertEqual(osi_model.datasets[0].unique_name, "Sales")
        self.assertEqual(len(osi_model.metrics), 1)
        self.assertEqual(osi_model.metrics[0].unique_name, "TotalAmount")
        print("SML -> OSI: OK")
        return osi_model

    def test_round_trip(self):
        print("\nTesting Round Trip (SML -> OSI -> SML)...")
        # SML -> OSI
        sml_to_osi = SMLToOSIConverter()
        osi_model = sml_to_osi.to_osi(self.sml_model)
        
        # OSI -> SML
        osi_to_sml = OSIToSMLConverter()
        new_sml_model = osi_to_sml.from_osi(osi_model)
        
        self.assertIsInstance(new_sml_model, SMLModel)
        self.assertEqual(new_sml_model.unique_name, "test_model")
        
        # Verify Datasets
        # Calendar might be injected, so check for "Sales" existence and count >= 1
        self.assertGreaterEqual(len(new_sml_model.datasets), 1) 
        sales_ds = next((d for d in new_sml_model.datasets if d.unique_name == "Sales"), None)
        self.assertIsNotNone(sales_ds)
        self.assertEqual(sales_ds.is_fact, True)
        
        # Verify Metrics
        self.assertEqual(len(new_sml_model.metrics), 1)
        self.assertEqual(new_sml_model.metrics[0].unique_name, "TotalAmount")
        
        print("Round Trip: OK")

if __name__ == '__main__':
    unittest.main()
