"""Date table/column resolution based on YAML config patterns."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
import os
import yaml

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class DateResolution:
    table: str
    date_col: str
    year_col: str
    month_col: str
    quarter_col: str
    monthindex_col: str


class DateResolutionConfig:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or Path(os.getenv("SEMABRIDGE_DATE_RESOLUTION", "Config/date_resolution.yaml"))
        self._loaded = False
        self._cfg: dict[str, list[str]] = {}

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            path = self.path
            if not path.is_absolute():
                path = (Path.cwd() / path).resolve()
            if not path.exists():
                logger.debug("Date resolution config not found at %s", path)
                return
            with open(path, "r", encoding="utf-8") as f:
                self._cfg = yaml.safe_load(f) or {}
        except Exception as exc:
            logger.warning("Failed to load date resolution config: %s", exc)

    def _patterns(self, key: str) -> list[str]:
        self._load()
        return [str(v).lower() for v in (self._cfg.get(key) or [])]

    def resolve(self, model: Any) -> Optional[DateResolution]:
        """Resolve date table and key columns from a model datasets list."""
        if not model or not getattr(model, "datasets", None):
            return None

        table_patterns = self._patterns("date_table_patterns")
        date_patterns = self._patterns("date_column_patterns")
        year_patterns = self._patterns("year_column_patterns")
        month_patterns = self._patterns("month_column_patterns")
        quarter_patterns = self._patterns("quarter_column_patterns")
        monthindex_patterns = self._patterns("monthindex_column_patterns")

        def _match(name: str, patterns: list[str]) -> bool:
            n = str(name or "").lower()
            return any(p in n for p in patterns) if patterns else False

        for ds in model.datasets:
            ds_name = str(getattr(ds, "unique_name", "") or "").lower()
            if table_patterns and not any(p in ds_name for p in table_patterns):
                continue

            date_col = year_col = month_col = quarter_col = monthindex_col = ""
            for col in getattr(ds, "columns", []) or []:
                col_name = str(getattr(col, "unique_name", "") or "")
                if not date_col and _match(col_name, date_patterns):
                    date_col = col_name
                if not year_col and _match(col_name, year_patterns):
                    year_col = col_name
                if not month_col and _match(col_name, month_patterns):
                    month_col = col_name
                if not quarter_col and _match(col_name, quarter_patterns):
                    quarter_col = col_name
                if not monthindex_col and _match(col_name, monthindex_patterns):
                    monthindex_col = col_name

            if date_col:
                return DateResolution(
                    table=ds.unique_name,
                    date_col=date_col,
                    year_col=year_col or "YEAR",
                    month_col=month_col or "MONTH",
                    quarter_col=quarter_col or "QUARTER",
                    monthindex_col=monthindex_col or "MONTHINDEX",
                )

        return None
