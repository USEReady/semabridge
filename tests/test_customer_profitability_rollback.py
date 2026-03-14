
import unittest
import shutil
import tempfile
import os
import json
import uuid
from pathlib import Path
from datetime import datetime

from semabridge.repository.duckdb_manager import DuckDBManager
from semabridge.sml.models import (
    SMLModel, SMLDataset, SMLColumn, SMLMetric, SMLDimension, 
    DataType, AggregationType, SourcePlatform
)

class TestCustomerProfitabilityRollback(unittest.TestCase):
    def setUp(self):
        # Create a temporary directory for the DuckDB repository
        self.test_dir = tempfile.mkdtemp()
        self.db_path = Path(self.test_dir) / "semabridge_test.db"
        
        # Initialize DuckDBManager with the test database path explicitly
        self.db_manager = DuckDBManager(db_path=str(self.db_path))
        self.project_id = "customer_profitability"
        self.workspace_id = "ws_cust_prof_001"

    def tearDown(self):
        # Clean up
        # DuckDBManager handles connections internally per method, no close needed on instance
        try:
            shutil.rmtree(self.test_dir)
        except PermissionError:
            # On Windows, open handles might prevent deletion immediately. 
            # In a real test suite we might need explicit gc.collect or just ignore
            pass

    def _create_v1_model(self):
        model = SMLModel(
            unique_name=self.project_id,
            label="Customer Profitability",
            description="Analyzes customer revenue and costs.",
            source_platform=SourcePlatform.SNOWFLAKE,
            version="1.0"
        )
        
        # 1. Customer Dimension
        customer = SMLDataset(
            unique_name="Customer",
            label="Customer",
            source_table="DIM_CUSTOMER",
            is_fact=False
        )
        customer.columns.append(SMLColumn(unique_name="CustomerID", data_type=DataType.INTEGER, is_key=True))
        customer.columns.append(SMLColumn(unique_name="Name", data_type=DataType.STRING))
        customer.columns.append(SMLColumn(unique_name="Segment", data_type=DataType.STRING))
        model.datasets.append(customer)
        
        # 2. Sales Fact
        sales = SMLDataset(
            unique_name="Sales",
            label="Sales",
            source_table="FACT_SALES",
            is_fact=True
        )
        sales.columns.append(SMLColumn(unique_name="OrderID", data_type=DataType.INTEGER, is_key=True))
        sales.columns.append(SMLColumn(unique_name="CustomerID", data_type=DataType.INTEGER)) # FK
        sales.columns.append(SMLColumn(unique_name="Amount", data_type=DataType.DECIMAL))
        model.datasets.append(sales)
        
        # 3. Basic Revenue Metric
        model.metrics.append(SMLMetric(
            unique_name="Total Revenue",
            label="Total Revenue",
            dataset="Sales",
            source_column="Amount",
            aggregation=AggregationType.SUM
        ))
        
        return model

    def _create_v2_model(self):
        # Start with V1 logic implies we extend it, but here we just construct the state
        model = self._create_v1_model()
        model.version = "2.0"
        
        # ADDED: Costs Fact
        costs = SMLDataset(
            unique_name="Costs",
            label="Costs",
            source_table="FACT_COSTS",
            is_fact=True
        )
        costs.columns.append(SMLColumn(unique_name="CostID", data_type=DataType.INTEGER, is_key=True))
        costs.columns.append(SMLColumn(unique_name="CustomerID", data_type=DataType.INTEGER))
        costs.columns.append(SMLColumn(unique_name="CostAmount", data_type=DataType.DECIMAL))
        model.datasets.append(costs)
        
        # ADDED: Total Cost Metric
        model.metrics.append(SMLMetric(
            unique_name="Total Cost",
            label="Total Cost",
            dataset="Costs",
            source_column="CostAmount",
            aggregation=AggregationType.SUM
        ))
        
        # ADDED: Profit Metric (Calculated)
        model.metrics.append(SMLMetric(
            unique_name="Profit",
            label="Customer Profit",
            dataset="Sales", # Logic-wise could be anywhere, simpler here
            expression="[Total Revenue] - [Total Cost]",
            aggregation=AggregationType.SUM # Placeholder
        ))
        
        return model

    def test_rollback_v2_to_v1(self):
        print("\n--- Testing Rollback: Customer Profitability ---")
        
        # 1. Commit V1
        v1_model = self._create_v1_model()
        self.db_manager.ensure_project(self.project_id, v1_model.label, self.workspace_id)
        
        run_id_v1 = str(uuid.uuid4())
        success_v1, snapshot_id_v1 = self.db_manager.commit_model(
            project_id=self.project_id,
            sml_json=v1_model.model_dump(mode='json'),
            run_id=run_id_v1,
            tag="v1.0"
        )
        print(f"Committed V1: {snapshot_id_v1} (Tag: v1.0)")
        self.assertTrue(success_v1)
        
        # 2. Commit V2 (The implementation of profitability)
        v2_model = self._create_v2_model()
        run_id_v2 = str(uuid.uuid4())
        success_v2, snapshot_id_v2 = self.db_manager.commit_model(
             project_id=self.project_id,
             sml_json=v2_model.model_dump(mode='json'),
             run_id=run_id_v2,
             tag="v2.0"
        )
        print(f"Committed V2: {snapshot_id_v2} (Tag: v2.0)")
        self.assertTrue(success_v2)
        
        # Verify V2 is HEAD
        head = self.db_manager.get_head(self.project_id)
        self.assertEqual(head.snapshot_id, snapshot_id_v2)
        self.assertEqual(len(head.sml_blob['metrics']), 3) # Revenue, Cost, Profit
        
        # 3. Perform Rollback to V1
        print("Rolling back to V1...")
        rollback_tag = "rollback_to_v1"
        success_rb, rb_snapshot_id, changes = self.db_manager.rollback(
            project_id=self.project_id,
            target_snapshot_id=snapshot_id_v1,
            tag=rollback_tag
        )
        
        print(f"Rollback Complete: {rb_snapshot_id}")
        self.assertTrue(success_rb)
        
        # 4. Verify State After Rollback
        head_after = self.db_manager.get_head(self.project_id)
        
        # It should NOT be the same snapshot ID as V1 (it's a new commit)
        self.assertNotEqual(head_after.snapshot_id, snapshot_id_v1)
        
        # But the CONTENT (SML) should be identical to V1
        current_metrics = [m['unique_name'] for m in head_after.sml_blob['metrics']]
        print(f"Metrics in HEAD: {current_metrics}")
        
        self.assertIn("Total Revenue", current_metrics)
        self.assertNotIn("Total Cost", current_metrics)
        self.assertNotIn("Profit", current_metrics)
        
        self.assertEqual(len(head_after.sml_blob['datasets']), 2) # Customer, Sales
        
        # Verify history trace
        history = self.db_manager.list_snapshots(self.project_id)
        self.assertEqual(len(history), 3) # v1, v2, rollback_v1
        self.assertEqual(history[0].version_tag, rollback_tag)

if __name__ == '__main__':
    unittest.main()
