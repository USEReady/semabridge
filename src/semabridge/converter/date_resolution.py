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
                # Relative to the working directory, else to the repo root —
                # a server started from another directory (e.g. src/) must
                # still find it, or every date anchor is silently skipped.
                candidate = (Path.cwd() / path).resolve()
                if not candidate.exists():
                    candidate = (Path(__file__).resolve().parents[3] / path).resolve()
                path = candidate
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
        effective_table_patterns = list(table_patterns) + (["year"] if "year" not in table_patterns else [])
        date_patterns = self._patterns("date_column_patterns")
        year_patterns = self._patterns("year_column_patterns")
        month_patterns = self._patterns("month_column_patterns")
        quarter_patterns = self._patterns("quarter_column_patterns")
        monthindex_patterns = self._patterns("monthindex_column_patterns")

        def _match(name: str, patterns: list[str]) -> bool:
            n = str(name or "").lower()
            return any(p in n for p in patterns) if patterns else False

        candidates: list[tuple[int, DateResolution]] = []
        relationships = list(getattr(model, "relationships", []) or [])

        for ds in model.datasets:
            ds_name = str(getattr(ds, "unique_name", "") or "").lower()
            if effective_table_patterns and not any(p in ds_name for p in effective_table_patterns):
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
                # Connected candidates take strict precedence over disconnected ones
                connected_rels = sum(
                    1 for r in relationships
                    if getattr(r, "is_active", True)
                    and (
                        str(getattr(r, "from_dataset", "") or getattr(r, "from_table", "")).casefold() == ds.unique_name.casefold()
                        or str(getattr(r, "to_dataset", "") or getattr(r, "to_table", "")).casefold() == ds.unique_name.casefold()
                    )
                )
                score = 0
                if connected_rels > 0:
                    score += 1000 + connected_rels * 10
                if _match(ds_name, ["dim_date", "calendar", "dates", "date"]):
                    score += 100
                cols_count = sum(1 for c in [date_col, year_col, month_col, quarter_col, monthindex_col] if c)
                score += cols_count * 10

                candidates.append((
                    score,
                    DateResolution(
                        table=ds.unique_name,
                        date_col=date_col,
                        year_col=year_col or "YEAR",
                        month_col=month_col or "MONTH",
                        quarter_col=quarter_col or "QUARTER",
                        monthindex_col=monthindex_col or "MONTHINDEX",
                    )
                ))

        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0][1]

        return None
