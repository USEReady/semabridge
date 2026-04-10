"""
Bootstrap a Databricks semantic-layer deployment pipeline with REST APIs.

This script performs the following steps:
1. Creates a workspace folder such as /Shared/SemaBridge_Pipeline.
2. Uploads a SQL notebook that creates semabridge.public.fact.
3. Uploads a local databricks_publisher.py file into the same folder.
4. Creates a Jobs API 2.1 multi-task job with:
   - Task 1: SQL notebook
   - Task 2: Python script, dependent on Task 1

The script uses only the `requests` library and Databricks REST APIs.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import requests


# ---------------------------------------------------------------------------
# Databricks connection settings
# Swap these values for your own workspace and PAT.
# ---------------------------------------------------------------------------
DATABRICKS_HOST = "https://adb-1234567890123456.7.azuredatabricks.net"
DATABRICKS_PAT = "dapiREPLACE_WITH_YOUR_PAT"


# ---------------------------------------------------------------------------
# Workspace asset locations
# ---------------------------------------------------------------------------
WORKSPACE_DIR = "/Shared/SemaBridge_Pipeline"
SQL_NOTEBOOK_PATH = f"{WORKSPACE_DIR}/build_fact_table"
PYTHON_SCRIPT_WORKSPACE_PATH = f"{WORKSPACE_DIR}/databricks_publisher.py"


# ---------------------------------------------------------------------------
# Local source file to upload for Task 2.
# By default this expects a file named databricks_publisher.py in the current
# working directory (for example, CI checkout root).
# ---------------------------------------------------------------------------
LOCAL_PUBLISHER_FILE = Path("databricks_publisher.py")


# ---------------------------------------------------------------------------
# Job settings
# These cluster settings are examples and usually need small adjustments
# for your Databricks cloud, runtime, and policy constraints.
# ---------------------------------------------------------------------------
JOB_NAME = "SemaBridge Semantic Layer Deployment"
JOB_CLUSTER_KEY = "semabridge_shared_cluster"
SPARK_VERSION = "14.3.x-scala2.12"
NODE_TYPE_ID = "Standard_DS3_v2"
NUM_WORKERS = 1


# ---------------------------------------------------------------------------
# Hardcoded SQL notebook contents for Task 1.
# Replace the table names in the FROM/JOIN clause with your actual sources.
# ---------------------------------------------------------------------------
FACT_BUILD_SQL = """
USE CATALOG semabridge;
USE SCHEMA public;

CREATE OR REPLACE TABLE semabridge.public.fact AS
SELECT
    k.customer_key,
    k.product_key,
    k.bu_key,
    k.scenario_key,
    t.transaction_id,
    t.transaction_date,
    t.yearperiod,
    t.revenue,
    t.material_costs,
    t.labor_costs_variable,
    t.taxes,
    t.rev_for_exp_travel,
    t.travel_expenses,
    t.cost_third_party,
    t.subscription_revenue
FROM semabridge.public.keys AS k
INNER JOIN semabridge.public.transactions AS t
    ON k.customer_key = t.customer_key
   AND k.product_key = t.product_key
   AND k.bu_key = t.bu_key
   AND k.scenario_key = t.scenario_key;
""".strip()


def build_headers() -> dict[str, str]:
    """Return standard headers for Databricks REST API calls."""
    return {
        "Authorization": f"Bearer {DATABRICKS_PAT}",
        "Content-Type": "application/json",
    }


def check_response(response: requests.Response, action: str) -> dict:
    """
    Validate a Databricks API response and return JSON on success.

    Raises:
        RuntimeError: If the response status code is not 2xx.
    """
    if not response.ok:
        message = response.text
        try:
            message = json.dumps(response.json(), indent=2)
        except ValueError:
            pass
        raise RuntimeError(
            f"{action} failed with HTTP {response.status_code}:\n{message}"
        )

    if not response.text.strip():
        return {}

    try:
        return response.json()
    except ValueError:
        return {}


def api_post(endpoint: str, payload: dict, action: str) -> dict:
    """POST JSON to a Databricks API endpoint and validate the result."""
    url = f"{DATABRICKS_HOST.rstrip('/')}{endpoint}"
    response = requests.post(url, headers=build_headers(), json=payload, timeout=60)
    return check_response(response, action)


def create_workspace_directory(path: str) -> None:
    """Create a workspace directory if it does not already exist."""
    payload = {"path": path}
    api_post(
        endpoint="/api/2.0/workspace/mkdirs",
        payload=payload,
        action=f"Creating workspace directory {path}",
    )
    print(f"Workspace directory is ready: {path}")


def upload_workspace_object(
    *,
    workspace_path: str,
    raw_content: bytes,
    language: str,
    overwrite: bool = True,
) -> None:
    """
    Upload a source file or notebook to the Databricks workspace.

    Databricks expects base64-encoded content for Workspace import requests.
    """
    encoded_content = base64.b64encode(raw_content).decode("utf-8")
    payload = {
        "path": workspace_path,
        "format": "SOURCE",
        "language": language,
        "content": encoded_content,
        "overwrite": overwrite,
    }
    api_post(
        endpoint="/api/2.0/workspace/import",
        payload=payload,
        action=f"Uploading workspace object {workspace_path}",
    )
    print(f"Uploaded workspace object: {workspace_path}")


def read_local_publisher_file(path: Path) -> bytes:
    """Read the local publisher script that will be uploaded for Task 2."""
    if not path.exists():
        raise FileNotFoundError(
            f"Local publisher file was not found: {path.resolve()}"
        )
    return path.read_bytes()


def create_multitask_job() -> int:
    """
    Create a Databricks Jobs API 2.1 multi-task job.

    Task 2 depends strictly on Task 1 succeeding.
    Both tasks use the same shared job cluster.
    """
    payload = {
        "name": JOB_NAME,
        "max_concurrent_runs": 1,
        "job_clusters": [
            {
                "job_cluster_key": JOB_CLUSTER_KEY,
                "new_cluster": {
                    "spark_version": SPARK_VERSION,
                    "node_type_id": NODE_TYPE_ID,
                    "num_workers": NUM_WORKERS,
                },
            }
        ],
        "tasks": [
            {
                "task_key": "build_fact_table",
                "description": "Create or replace semabridge.public.fact from source tables.",
                "job_cluster_key": JOB_CLUSTER_KEY,
                "notebook_task": {
                    "notebook_path": SQL_NOTEBOOK_PATH,
                    "source": "WORKSPACE",
                },
            },
            {
                "task_key": "publish_metric_views",
                "description": "Publish metric views after the fact table is built.",
                "depends_on": [
                    {
                        "task_key": "build_fact_table",
                    }
                ],
                "job_cluster_key": JOB_CLUSTER_KEY,
                "spark_python_task": {
                    "python_file": PYTHON_SCRIPT_WORKSPACE_PATH,
                    "source": "WORKSPACE",
                    "parameters": [],
                },
            },
        ],
    }

    result = api_post(
        endpoint="/api/2.1/jobs/create",
        payload=payload,
        action=f"Creating Databricks job {JOB_NAME}",
    )
    job_id = result.get("job_id")
    if job_id is None:
        raise RuntimeError(f"Databricks job creation succeeded but no job_id was returned: {result}")

    print(f"Created Databricks job: {JOB_NAME}")
    print(f"Job ID: {job_id}")
    return int(job_id)


def main() -> None:
    """Run the bootstrap workflow end to end."""
    print("Starting Databricks bootstrap...")

    create_workspace_directory(WORKSPACE_DIR)

    upload_workspace_object(
        workspace_path=SQL_NOTEBOOK_PATH,
        raw_content=FACT_BUILD_SQL.encode("utf-8"),
        language="SQL",
    )

    publisher_bytes = read_local_publisher_file(LOCAL_PUBLISHER_FILE)
    upload_workspace_object(
        workspace_path=PYTHON_SCRIPT_WORKSPACE_PATH,
        raw_content=publisher_bytes,
        language="PYTHON",
    )

    create_multitask_job()
    print("Databricks bootstrap completed successfully.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Bootstrap failed: {exc}")
        raise
