"""Precompute-aggregation override helpers.

Mirrors utils/synonyms.py's load/lookup shape exactly, for the same reason:
a project-scoped override table that's optional for compatibility with
older deployments -- a missing table/model/schema error is logged and
treated as "no override," never raised, so a DB-layer issue here can't
break DDL emission.

What this overrides: when SnowflakeEmitter._build_precomputed_column_select
finds a cross-table lookup whose join key isn't a confirmed unique key, it
collapses the related table to one row per key with an aggregate function
(AVG for numeric columns, MODE for everything else, by default) before
joining. This module lets a specific project override that choice for a
specific column without a code change.
"""

from __future__ import annotations

from typing import Iterable

from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

VALID_AGGREGATION_STRATEGIES = ("AVG", "SUM", "MIN", "MAX", "MODE")


def precompute_aggregation_override_key(model_name: str, table_name: str, column_name: str) -> tuple[str, str, str]:
    return (
        str(model_name or "").strip().casefold(),
        str(table_name or "").strip().casefold(),
        str(column_name or "").strip().casefold(),
    )


def load_precompute_aggregation_overrides(project_id: str | None) -> dict[tuple[str, str, str], str]:
    """Load precompute-aggregation overrides for a project.

    The override table is optional for compatibility with older
    deployments. Missing table/model/schema errors are logged and treated
    as no overrides.
    """
    project_id = str(project_id or "").strip()
    if not project_id:
        return {}

    try:
        from sqlalchemy import select

        from semabridge.repository.orm.models import PrecomputeAggregationOverride
        from semabridge.repository.orm.session_factory import db_manager

        overrides: dict[tuple[str, str, str], str] = {}
        with db_manager.get_session() as session:
            rows = session.execute(
                select(PrecomputeAggregationOverride).where(
                    PrecomputeAggregationOverride.project_id == project_id
                )
            ).scalars().all()
            for row in rows:
                strategy = str(row.aggregation_strategy or "").strip().upper()
                if strategy not in VALID_AGGREGATION_STRATEGIES:
                    logger.warning(
                        "Ignoring precompute_aggregation_override with unrecognized strategy %r for %s.%s",
                        strategy, row.table_name, row.column_name,
                    )
                    continue
                overrides[
                    precompute_aggregation_override_key(row.model_name, row.table_name, row.column_name)
                ] = strategy
        return overrides
    except Exception as exc:
        logger.warning(
            "Precompute-aggregation override table unavailable for project '%s'; continuing without overrides: %s",
            project_id,
            exc,
        )
        return {}


def lookup_precompute_aggregation_override(
    overrides: dict[tuple[str, str, str], str] | None,
    model_names: Iterable[str],
    table_name: str,
    column_name: str,
) -> str | None:
    if not overrides:
        return None
    for model_name in model_names:
        found = overrides.get(precompute_aggregation_override_key(model_name, table_name, column_name))
        if found:
            return found
    return None
