
import sys
import os
import uuid
import json
from pathlib import Path
from datetime import datetime

# Add src to path
sys.path.insert(0, os.path.join(os.getcwd(), "src"))

from semabridge.repository.duckdb_manager import DuckDBManager
from semabridge.sml.models import (
    SMLModel, SMLDataset, SMLColumn, SMLMetric, 
    DataType, AggregationType, SourcePlatform
)

def setup_test_state():
    print("Setting up 'Customer Profitability' project for rollback test...")
    
    # Use the real DB
    db_manager = DuckDBManager() # Uses default path
    project_id = "Customer Profitability"
    
    # --- Version 1 (Baseline) ---
    v1_model = SMLModel(
        unique_name=project_id,
        label="Customer Profitability",
        description="Analyzes customer revenue.",
        source_platform=SourcePlatform.SNOWFLAKE,
        version="1.0"
    )
    
    # Dataset: Sales
    sales = SMLDataset(unique_name="Sales", label="Sales", source_table="FACT_SALES", is_fact=True)
    sales.columns.append(SMLColumn(unique_name="Amount", data_type=DataType.DECIMAL))
    v1_model.datasets.append(sales)
    
    # Metric: Revenue
    v1_model.metrics.append(SMLMetric(
        unique_name="Total Revenue",
        label="Total Revenue",
        dataset="Sales",
        source_column="Amount",
        aggregation=AggregationType.SUM
    ))
    
    db_manager.ensure_project(project_id, "Customer Profitability", "ws_test")
    
    success, v1_id = db_manager.commit_model(
        project_id=project_id,
        sml_json=v1_model.model_dump(mode='json'),
        tag="v1.0",
        status="success"
    )
    print(f"Committed v1.0 (Baseline): {v1_id}")
    
    # --- Version 2 (Manual 'Bad' Change) ---
    v2_model = v1_model.model_copy(deep=True)
    v2_model.version = "2.0"
    
    # Add 'Bad' Metric
    v2_model.metrics.append(SMLMetric(
        unique_name="Incorrect Profit",
        label="Incorrect Profit",
        dataset="Sales",
        expression="[Amount] * 999", # Obviously wrong
        aggregation=AggregationType.SUM
    ))
    
    success, v2_id = db_manager.commit_model(
        project_id=project_id,
        sml_json=v2_model.model_dump(mode='json'),
        tag="v2.0",
        status="success"
    )
    print(f"Committed v2.0 (With 'Incorrect Profit'): {v2_id}")
    
    print("\nState setup complete!")
    print(f"Project: '{project_id}'")
    print(f"Current HEAD is v2.0. You can now test rollback to v1.0.")

if __name__ == "__main__":
    setup_test_state()
