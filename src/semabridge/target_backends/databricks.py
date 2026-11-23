"""Databricks backend: Unity Catalog metric views.

Phase 3.1 provides only the rules; table matching, planning, YAML writing,
deploy and verification are added in steps 3.4-3.9. Used only when
``behavior.databricks.use_planner_path`` is on (default off).
"""

from __future__ import annotations

from semabridge.target_backends.base import TargetBackend


class DatabricksBackend(TargetBackend):
    target = "databricks"
