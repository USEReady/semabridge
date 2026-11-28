"""Genie Space Configuration and Mapping Manager."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from semabridge.connectors.genie.client import GenieClient
from semabridge.connectors.genie.models import GenieSpace

logger = logging.getLogger(__name__)


class GenieSpaceManager:
    """Manages Genie Space configuration, mapping to metric views, and space creation."""

    def __init__(self, client: GenieClient) -> None:
        self.client = client

    def get_space(self, space_id: str) -> Optional[GenieSpace]:
        """Retrieve details for a specific space ID."""
        try:
            data = self.client.get_space(space_id)
            return GenieSpace.from_api_dict(data)
        except Exception as exc:
            logger.error("Failed to retrieve Genie Space %s: %s", space_id, exc)
            return None

    def map_space_to_metric_view(
        self,
        space_id: str,
        metric_view: str,
    ) -> Dict[str, Any]:
        """Map a Genie Space to a target metric view for automated benchmark publishing."""
        space = self.get_space(space_id)
        if not space:
            raise ValueError(f"Genie Space '{space_id}' not found.")

        current_views = list(space.metric_views)
        if metric_view not in current_views:
            current_views.append(metric_view)

        return {
            "space_id": space_id,
            "title": space.title,
            "metric_view": metric_view,
            "all_metric_views": current_views,
            "warehouse_id": space.warehouse_id,
            "mapped": True,
        }

    def ensure_space(
        self,
        title: str,
        warehouse_id: str,
        metric_views: Optional[List[str]] = None,
        description: str = "Managed by SemaBridge",
    ) -> GenieSpace:
        """Create a Genie Space if one with the given title does not already exist."""
        existing_spaces = self.client.list_spaces()
        for s_data in existing_spaces:
            if s_data.get("title") == title or s_data.get("name") == title:
                return GenieSpace.from_api_dict(s_data)

        # Create new space
        created_data = self.client.create_space(
            title=title,
            warehouse_id=warehouse_id,
            description=description,
            metric_views=metric_views,
        )
        return GenieSpace.from_api_dict(created_data)
