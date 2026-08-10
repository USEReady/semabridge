"""Shared types for DaxTranslationService.

New module — part of the additive Step 1 build. Nothing here changes any
existing pipeline's behavior; these types are only consumed by the new
dax_translation package itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class Dialect(str, Enum):
    """Target SQL dialect for a translation request."""

    SNOWFLAKE = "snowflake"
    DATABRICKS = "databricks"

    @classmethod
    def coerce(cls, value: "Dialect | str") -> "Dialect":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            raise ValueError(
                f"Unsupported dialect {value!r}; expected one of "
                f"{[d.value for d in cls]}"
            ) from exc


@dataclass
class TranslationRequest:
    """Everything a translation attempt needs, for any tier, any dialect.

    dataset_col_lookup and dataset_aliases are required (not optional) —
    this is what makes Tier 5's semantic column-existence validation
    possible for every caller, closing the gap where the old extraction-time
    pipeline (converter/dax_translator.py) had no schema to validate
    against at all.
    """

    dax: str
    dataset_name: str
    table_alias: str
    dataset_col_lookup: Dict[str, Set[str]]
    dataset_aliases: Dict[str, str]
    metric_name: Optional[str] = None
    dialect: Dialect = Dialect.SNOWFLAKE
    metrics_context: List[Any] = field(default_factory=list)
    metric_names: Optional[Set[str]] = None
    # Shape tuple (see converter/time_intelligence_shapes.py) -> precomputed
    # flag column name on the current fact table's enriched view. Lets Tier
    # 3's AST renderer reference that column instead of inline MAX_DATE
    # arithmetic, which Snowflake's semantic-view compiler rejects even
    # though the column physically exists. None/empty preserves the
    # inline-MAX_DATE rendering exactly.
    anchor_flag_map: Optional[Dict[Any, str]] = None
    # dataset -> {SANITIZED_COLUMN: TYPE_TOKEN} (e.g. "INTEGER", "DATE") --
    # the type-carrying sibling of dataset_col_lookup, built the same way
    # connectors/type_safety_validator.py's build_dataset_col_types() builds
    # it for DDL-emission-time validation. Optional and additive, same as
    # anchor_flag_map above: None/empty preserves the existing name-only
    # schema-context rendering in tier5/prompt.py exactly; populated by a
    # caller, it lets the Tier 5 prompt tell the LLM which columns are
    # DATE-typed vs. INTEGER/NUMBER-typed, so it stops guessing and
    # producing date-arithmetic compared against an integer surrogate key
    # (the real incident type_safety_validator.py's DDL-emission check
    # catches after the fact -- this is the earlier, prevention-side half).
    dataset_col_types: Optional[Dict[str, Dict[str, str]]] = None

    def __post_init__(self) -> None:
        self.dialect = Dialect.coerce(self.dialect)


@dataclass
class TranslationResult:
    """Result of a translation attempt, carrying enough metadata to feed
    the complexity_tier UI (tier/provider/confidence) once wired up."""

    sql: Optional[str]
    tier: int  # 1-4 deterministic, 5 = LLM
    original_dax: str
    is_success: bool = False
    provider: Optional[str] = None  # None for tiers 1-4; adapter name for tier 5
    translation_provider_confidence: float = 1.0
    validation_notes: List[str] = field(default_factory=list)
    source_pipeline: str = ""  # migration bookkeeping only ("A"/"B"/"C"); droppable once unified

    def __post_init__(self) -> None:
        if self.sql is not None:
            self.is_success = True
        if not (1 <= self.tier <= 5):
            raise ValueError(f"tier must be 1-5, got {self.tier}")
