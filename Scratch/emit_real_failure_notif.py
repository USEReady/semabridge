import time
import os
import unittest.mock
from dotenv import load_dotenv
load_dotenv()

from semabridge.sml.models import SMLModel, SMLDataset, SMLMetric
from semabridge.core.engine.context import RunContext
from semabridge.core.settings import get_settings
from semabridge.core.behavior import ConnectorBehavior
from semabridge.core.engine.engine import ExecutionEngine
from semabridge.core.run_summary import RunStatus

def main():
    print("Building SMLModel dynamically...")
    model = SMLModel(
        unique_name="competative-sync-notif",
        label="competative-sync-notif",
        datasets=[
            SMLDataset(unique_name="dataset_1"),
            SMLDataset(unique_name="dataset_2")
        ],
        metrics=[
            SMLMetric(unique_name="metric_1", dataset="dataset_1"),
            SMLMetric(unique_name="metric_2", dataset="dataset_1")
        ]
    )

    print("Constructing RunContext...")
    import uuid as _uuid
    run_uuid = f"run-debug-notif-failed-{_uuid.uuid4()}"
    context = RunContext(
        project_id="competative-sync-notif",
        run_id=run_uuid,
        config=get_settings(),
        start_time=time.time() - 15.0, # mock 15 second duration
        source_type="fabric",
        target_type="snowflake",
        behavior=ConnectorBehavior(),
        sml_model=model,
        sync_mode="copy"
    )
    
    print("Instantiating ExecutionEngine...")
    engine = ExecutionEngine()
    engine.db_manager = unittest.mock.MagicMock() # prevent database operations
    
    # Mocking errors in summary to simulate a real failure error message
    from semabridge.core.run_summary import create_run_summary
    summary = create_run_summary(
        project_id="competative-sync-notif",
        run_id=run_uuid,
        source_type="fabric",
        target_type="snowflake"
    )
    summary.errors = [{"message": "Database authentication timed out on Snowflake connection pool"}]
    engine._summary = summary
    
    print("Triggering _step10_finalize with failure...")
    summary = engine._step10_finalize(context, RunStatus.FAILED)
    print("Step 10 Finalization successfully completed with failure!")
    print(f"Summary status: {summary.status}")

if __name__ == "__main__":
    main()
