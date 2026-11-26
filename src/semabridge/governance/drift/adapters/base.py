"""Platform Adapter Base Interface.

Defines the contract that every warehouse platform adapter must implement.
Fetching raw definition text and parsing are strictly separated so the exact
same parser parses both the live warehouse definition and the stored baseline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from semabridge.governance.drift.models import ViewDefinition


class PlatformAdapter(ABC):
    """Abstract interface for platform-specific metadata reading and definition parsing."""

    platform_name: str = "UNKNOWN"

    @abstractmethod
    def list_views(self, project_id: str, scope: Optional[Dict[str, str]] = None) -> List[str]:
        """List all managed view names within the specified project or scope."""
        raise NotImplementedError

    @abstractmethod
    def fetch_definition_text(
        self,
        view_name: str,
        connection_config: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Fetch raw definition text (DDL/YAML) from the warehouse.

        This is the only method that calls the target warehouse.
        """
        raise NotImplementedError

    @abstractmethod
    def parse_definition(
        self,
        raw_text: str,
        view_name: str,
        source: str = "live",
        descriptions: Optional[Dict[str, str]] = None,
    ) -> ViewDefinition:
        """Parse raw definition text into a canonical ViewDefinition.

        Pure function; performs no network or warehouse operations.
        """
        raise NotImplementedError

    def get_definition(
        self,
        view_name: str,
        connection_config: Optional[Dict[str, Any]] = None,
        source: str = "live",
        force_refresh: bool = False,
    ) -> ViewDefinition:
        """Fetch definition from warehouse (with TTL caching) and parse into canonical ViewDefinition."""
        from semabridge.governance.drift.introspection_cache import (
            derive_connection_identity,
            get_cached_definition,
            set_cached_definition,
        )

        identity = derive_connection_identity(connection_config)
        cached_text = get_cached_definition(
            self.platform_name, identity, view_name, force_refresh=force_refresh
        )
        if cached_text is not None:
            text = cached_text
        else:
            text = self.fetch_definition_text(view_name, connection_config=connection_config)
            if text:
                set_cached_definition(self.platform_name, identity, view_name, text)

        return self.parse_definition(text, view_name=view_name, source=source)

    def list_consumers(
        self,
        view_name: str,
        connection_config: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """List downstream consumer assets (e.g. data shares, dashboards, Cortex models).

        Defaults to an empty list when unconfigured or unsupported.
        """
        return []

    def inject_verified_queries(
        self,
        pairs: List[Dict[str, Any]],
        view_name: str,
        connection_config: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Inject verified question/SQL pairs into downstream consumers (Cortex Analyst or Genie).

        Default implementation refuses with an unsupported platform reason.
        """
        return {
            "supported": False,
            "injected_count": 0,
            "reason": f"Platform '{self.platform_name}' does not support automated verified query injection.",
            "target": None,
            "details": [],
            "dry_run": dry_run,
        }
