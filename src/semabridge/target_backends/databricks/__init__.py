"""Databricks backend: Unity Catalog metric views.

    guard.py        validate_databricks_sql: deny-by-default SQL guard (3.3)
    auth.py         access tokens: PAT, service principal (OAuth M2M), interactive
    client.py       guarded SQL Statement Execution client (3.3)
    warehouses.py   SQL warehouse discovery and choice (3.3)
    connection.py   non-waking connection test and permission audit (3.3)

Table matching, planning, YAML writing, deploy and verification are added
in steps 3.4-3.9. The backend is used only when
``behavior.databricks.use_planner_path`` is on (default off).
"""

from __future__ import annotations

from semabridge.target_backends.base import TargetBackend


class DatabricksBackend(TargetBackend):
    target = "databricks"


__all__ = ["DatabricksBackend"]
