"""Recorded Fixture Adapter for Drift Monitoring Mock Mode.

Replays recorded definition files (DDL/YAML) from a configured fixture directory
for offline testing without calling any live warehouse.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from semabridge.governance.drift.adapters.base import PlatformAdapter
from semabridge.governance.drift.adapters.databricks import DatabricksAdapter
from semabridge.governance.drift.adapters.snowflake import SnowflakeAdapter
from semabridge.governance.drift.models import ViewDefinition

logger = logging.getLogger("semabridge.governance.drift.adapters.fixture")

_SUPPORTED_EXTS = (".sql", ".yaml", ".yml", ".ddl", ".json", ".txt")


class RecordedFixtureAdapter(PlatformAdapter):
    """Adapter that reads recorded definition files for offline mock drift verification."""

    def __init__(
        self,
        platform: str = "SNOWFLAKE",
        fixture_dir: Optional[str] = None,
    ) -> None:
        self.platform = str(platform or "SNOWFLAKE").upper().strip()
        self.platform_name = f"FIXTURE_{self.platform}"

        env_dir = os.getenv("SEMABRIDGE_DRIFT_FIXTURE_DIR")
        if fixture_dir:
            self.fixture_dir = Path(fixture_dir)
        elif env_dir:
            self.fixture_dir = Path(env_dir)
        else:
            self.fixture_dir = Path(__file__).parent.parent / "fixtures"

        if self.platform == "DATABRICKS":
            self._parser: PlatformAdapter = DatabricksAdapter()
        else:
            self._parser = SnowflakeAdapter()

    def _clean_view_name(self, view_name: str) -> str:
        parts = [p.strip().strip('"').strip('`') for p in str(view_name).split(".") if p.strip()]
        return parts[-1] if parts else str(view_name).strip()

    def list_views(self, project_id: str, scope: Optional[Dict[str, str]] = None) -> List[str]:
        """List view names available in the recorded fixtures."""
        views: List[str] = []
        search_dirs = [
            self.fixture_dir / self.platform.lower(),
            self.fixture_dir / self.platform,
            self.fixture_dir,
        ]
        for d in search_dirs:
            if not d.is_dir():
                continue
            for f in d.iterdir():
                if f.is_file() and f.suffix.lower() in _SUPPORTED_EXTS:
                    v_stem = f.stem
                    if v_stem not in views:
                        views.append(v_stem)
        return sorted(views)

    def fetch_definition_text(
        self,
        view_name: str,
        connection_config: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Read the recorded view definition file from the fixture directory."""
        clean_name = self._clean_view_name(view_name).lower()
        search_dirs = [
            self.fixture_dir / self.platform.lower(),
            self.fixture_dir / self.platform,
            self.fixture_dir,
        ]

        for d in search_dirs:
            if not d.is_dir():
                continue
            # Exact or clean stem match
            for f in d.iterdir():
                if f.is_file() and f.suffix.lower() in _SUPPORTED_EXTS:
                    if f.stem.lower() == clean_name or f.stem.lower() == str(view_name).lower():
                        logger.info("Loaded drift mock fixture from %s", f)
                        return f.read_text(encoding="utf-8")

        raise RuntimeError(
            f"Fixture definition not found for view '{view_name}' on platform {self.platform} "
            f"in fixture directory '{self.fixture_dir}'"
        )

    def parse_definition(
        self,
        raw_text: str,
        view_name: str,
        source: str = "fixture",
        descriptions: Optional[Dict[str, str]] = None,
    ) -> ViewDefinition:
        """Parse recorded definition text with the platform parser, setting source to 'fixture'."""
        parsed = self._parser.parse_definition(
            raw_text,
            view_name=view_name,
            source=source,
            descriptions=descriptions,
        )
        parsed.source = "fixture"
        return parsed

    def get_definition(
        self,
        view_name: str,
        connection_config: Optional[Dict[str, Any]] = None,
        source: str = "fixture",
        force_refresh: bool = False,
    ) -> ViewDefinition:
        """Fetch from fixture files directly without warehouse or TTL caching."""
        raw_text = self.fetch_definition_text(view_name, connection_config=connection_config)
        return self.parse_definition(raw_text, view_name=view_name, source=source)
