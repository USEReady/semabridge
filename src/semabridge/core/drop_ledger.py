"""
Uniform "what got dropped and why" tracking across the sync/deploy pipeline.

Many independent stages can silently exclude an entity (table, column,
metric, or relationship) from the final deployed DDL — DAX/SQL translation
failures, live-schema mismatches, DDL-emission-time skips (unsupported SQL
shapes, unresolved references, hardcoded exclusions), and DDL-deployment-time
rejections by Snowflake itself. Historically each of these reported its
reason (if at all) only to a logger or stdout, with no structured record
ever reaching the API or UI — a user had no way to discover a drop without
manually diffing output files.

DropLedger is the single collection point: any call site that already
computes a human-readable reason string calls `ledger.record(...)` once, and
the aggregated list flows through `RunSummary.dropped_entities` (post-deploy)
or `build_entity_mappings()`'s return (dry-run trial pass) to the API/UI,
regardless of which stage or mechanism produced the drop. No per-mechanism
plumbing is needed beyond that one call — this is what makes the feature
generalize to any model and any drop cause instead of being hardcoded to
DAX/measures.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DropStage(str, Enum):
    """Pipeline stage at which an entity was excluded from deployed DDL."""

    EXTRACTION = "extraction"
    DAX_TRANSLATION = "dax_translation"
    SCHEMA_VALIDATION = "schema_validation"
    DDL_EMISSION = "ddl_emission"
    DDL_DEPLOYMENT = "ddl_deployment"


class DropRecord(BaseModel):
    """A single entity excluded from the final deployed DDL, and why."""

    entity_kind: str  # "table" | "column" | "metric" | "relationship"
    entity_name: str
    dataset: Optional[str] = None
    stage: DropStage
    reason: str
    detail: Optional[str] = None
    # True for intentional, by-design exclusions (e.g. Power BI's own
    # auto-generated date-table shadows) — these are not failures and should
    # be shown separately/de-emphasized rather than alarmed on.
    by_design: bool = False


class DropLedger:
    """Run-scoped collector for DropRecord entries.

    Callers that don't need to share a ledger across multiple builders can
    simply omit it — every constructor that accepts one falls back to a
    private DropLedger() of its own, so `self.drop_ledger.record(...)` is
    always safe to call unconditionally, no None-checks required at any
    call site.
    """

    def __init__(self) -> None:
        self.records: List[DropRecord] = []

    def record(
        self,
        entity_kind: str,
        entity_name: Any,
        stage: DropStage,
        reason: str,
        *,
        dataset: Optional[str] = None,
        detail: Optional[str] = None,
        by_design: bool = False,
    ) -> None:
        self.records.append(
            DropRecord(
                entity_kind=entity_kind,
                entity_name=str(entity_name),
                dataset=dataset,
                stage=stage,
                reason=reason,
                detail=detail,
                by_design=by_design,
            )
        )

    def extend(self, other: Optional["DropLedger"]) -> None:
        """Merge another ledger's records into this one (for sub-passes)."""
        if other is None:
            return
        self.records.extend(other.records)

    def clear(self) -> None:
        """Empty this ledger in place — for reused instances (e.g. a
        long-lived emitter object reset at the start of each deploy) that
        must keep the same object identity shared with components already
        constructed against it."""
        self.records.clear()

    def to_json(self) -> List[Dict[str, Any]]:
        return [r.model_dump(mode="json") for r in self.records]
