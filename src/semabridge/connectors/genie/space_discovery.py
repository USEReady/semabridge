"""Genie Space Discovery Service."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from semabridge.connectors.genie.client import GenieClient
from semabridge.connectors.genie.models import GenieSpace

logger = logging.getLogger(__name__)


class SpaceDiscoveryService:
    """Discovers and inspects Genie Spaces in a Databricks workspace."""

    def __init__(self, client: GenieClient) -> None:
        self.client = client

    def list_spaces(self) -> List[GenieSpace]:
        """Discover all available Genie Spaces."""
        try:
            raw_spaces = self.client.list_spaces()
            return [GenieSpace.from_api_dict(s) for s in raw_spaces]
        except Exception as exc:
            logger.warning("Failed to discover Genie spaces: %s", exc)
            return []

    def find_space_for_metric_view(
        self,
        metric_view_name: str,
        warehouse_id: Optional[str] = None,
    ) -> Optional[GenieSpace]:
        """Locate the Genie Space configured for a specific metric view or warehouse."""
        spaces = self.list_spaces()
        if not spaces:
            return None

        clean_target = str(metric_view_name or "").strip().lower()
        target_stem = clean_target.split(".")[-1]

        # 1. Exact metric_views list match
        for s in spaces:
            for mv in s.metric_views:
                clean_mv = str(mv).strip().lower()
                if clean_mv == clean_target or clean_mv.endswith(f".{target_stem}") or target_stem == clean_mv:
                    return s

        # 2. Match by title / display name
        for s in spaces:
            clean_title = s.title.strip().lower()
            if target_stem in clean_title or clean_title in target_stem:
                return s

        # 3. Match by warehouse_id if specified
        if warehouse_id:
            for s in spaces:
                if s.warehouse_id == warehouse_id:
                    return s

        # Return the first available space as candidate if only one exists
        if len(spaces) == 1:
            return spaces[0]

        return None
