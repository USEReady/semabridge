"""Canonical Baseline Lifecycle Store.

Manages post-deployment baselines in PostgreSQL (`drift_baselines` table).
Baselines are strictly captured at deployment time from the final executed
DDL or YAML that succeeded on the target warehouse.

Rules:
- A normal drift check NEVER overwrites an active baseline.
- Every new successful deployment increments the baseline version and preserves history.
- Auto-remediated / dropped objects are recorded in `not_deployed_objects` so they
do not trigger false DELETION drift findings.
- Baseline retrieval never falls back to arbitrary local files or unverified ontologies.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("semabridge.governance.drift.baseline_store")

DEFAULT_MAX_BASELINE_HISTORY = 50


def get_max_baseline_history() -> int:
    """Retrieve maximum baseline history snapshots to retain per view from configuration."""
    raw = os.environ.get("SEMABRIDGE_DRIFT_MAX_BASELINE_HISTORY")
    if raw:
        try:
            val = int(raw.strip())
            if val > 0:
                return val
        except ValueError:
            logger.warning("Invalid SEMABRIDGE_DRIFT_MAX_BASELINE_HISTORY '%s'; using default %d", raw, DEFAULT_MAX_BASELINE_HISTORY)
    return DEFAULT_MAX_BASELINE_HISTORY


_drift_tables_ready: set = set()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pkey(project_id: Optional[str]) -> str:
    """Normalize a project identifier by stripping prefix and lowercase."""
    return str(project_id or "").lower().removeprefix("proj-").strip()


def _full_view_key(view_name: Optional[str]) -> str:
    """Normalize a view name to comparable canonical case-folded key without quotes."""
    if not view_name:
        return ""
    parts = [p.strip().strip('"').strip("`").casefold() for p in str(view_name).split(".") if p.strip()]
    return ".".join(parts)


def _ensure_tables() -> None:
    """Ensure drift_baselines table exists in the database."""
    try:
        from semabridge.repository.orm.base import Base
        from semabridge.repository.orm.models import DriftBaselineRow
        from semabridge.repository.orm.session_factory import db_manager

        engine = db_manager.get_engine()
        if id(engine) in _drift_tables_ready:
            return
        Base.metadata.create_all(
            bind=engine,
            tables=[DriftBaselineRow.__table__],
            checkfirst=True,
        )
        _drift_tables_ready.add(id(engine))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not verify drift_baselines table in database: %s", exc)


def _get_db_session():
    """Retrieve an ORM session from the shared session factory."""
    from semabridge.repository.orm.session_factory import db_manager
    return db_manager.get_session()


def save_deploy_baseline(
    project_id: str,
    platform: str,
    view_name: str,
    definition_text: Optional[str] = None,
    not_deployed_objects: Optional[List[str]] = None,
    snapshot_id: Optional[str] = None,
    deployed_at: Optional[str] = None,
    metrics: Optional[Dict[str, str]] = None,
    descriptions: Optional[Dict[str, str]] = None,
    source_definitions: Optional[Dict[str, str]] = None,
    baseline_source: str = "deploy",
) -> Dict[str, Any]:
    """Persist the canonical baseline after a successful deployment.

    Args:
        project_id: Canonical project identifier.
        platform: Warehouse platform (e.g. SNOWFLAKE, DATABRICKS).
        view_name: Fully qualified view name.
        definition_text: Exact DDL or YAML successfully executed on warehouse.
        not_deployed_objects: Objects intentionally dropped or nulled during remediation.
        snapshot_id: Associated metadata snapshot ID.
        deployed_at: ISO timestamp of deployment.
        metrics: Extracted or provided metric expressions.
        descriptions: Extracted or provided metric/object descriptions.
        source_definitions: Original source expressions for reconciliation.

    Returns:
        The active baseline dictionary.
    """
    if not project_id or not str(project_id).strip():
        raise ValueError("Cannot save deploy baseline: project_id is required")
    if not view_name or not str(view_name).strip():
        raise ValueError("Cannot save deploy baseline: view_name is required")

    p_clean = _pkey(project_id)
    full_v = _full_view_key(view_name)
    platform_norm = str(platform or "UNKNOWN").upper().strip()
    # Platform-qualified ID guarantees zero cross-warehouse overwrites or competition
    plat_row_id = f"{p_clean}:{platform_norm}:{full_v}"
    legacy_row_id = f"{p_clean}:{full_v}"
    now_iso = deployed_at or _utc_now_iso()

    clean_metrics = {str(k).upper(): str(v).strip() for k, v in (metrics or {}).items()}
    clean_descriptions = {str(k).upper(): str(v).strip() for k, v in (descriptions or {}).items()}
    clean_sources = {str(k).upper(): str(v).strip() for k, v in (source_definitions or {}).items()}
    clean_not_deployed = sorted(list({str(o).upper().strip() for o in (not_deployed_objects or []) if o}))

    version = 1
    history: List[Dict[str, Any]] = []

    if is_test_memory_backend_enabled():
        fallback_entry: Dict[str, Any] = {
            "project_id": str(project_id).strip(),
            "platform": platform_norm,
            "view_name": str(view_name).strip(),
            "semantic_view": str(view_name).strip(),
            "version": version,
            "definition_text": definition_text or "",
            "not_deployed_objects": clean_not_deployed,
            "snapshot_id": snapshot_id,
            "deployed_at": now_iso,
            "baseline_source": baseline_source,
            "metrics": clean_metrics,
            "descriptions": clean_descriptions,
            "source_definitions": clean_sources,
            "history": history,
            "updated_at": now_iso,
        }
        _sync_fallback_cache(p_clean, full_v, fallback_entry, platform=platform_norm)
        return fallback_entry

    # Attempt to load existing row to track versioning and historical entries
    try:
        from semabridge.repository.orm.models import DriftBaselineRow
        _ensure_tables()
        with _get_db_session() as session:
            existing_row = session.get(DriftBaselineRow, plat_row_id) or session.get(DriftBaselineRow, legacy_row_id)
            if existing_row:
                version = (existing_row.version or 1) + 1
                try:
                    old_payload = json.loads(existing_row.payload_json)
                    old_history = old_payload.get("history") or []
                    history = list(old_history)
                    # Snapshot the previous version into history
                    history.append({
                        "version": existing_row.version or 1,
                        "deployed_at": old_payload.get("deployed_at"),
                        "snapshot_id": old_payload.get("snapshot_id"),
                        "metrics_count": len(old_payload.get("metrics") or {}),
                        "not_deployed_count": len(old_payload.get("not_deployed_objects") or []),
                    })
                    # Retain up to configured maximum historical snapshots
                    max_hist = get_max_baseline_history()
                    if len(history) > max_hist:
                        history = history[-max_hist:]
                except Exception as ex:
                    logger.warning("Could not read previous baseline history for %s: %s", plat_row_id, ex)

            baseline_entry: Dict[str, Any] = {
                "project_id": str(project_id).strip(),
                "platform": platform_norm,
                "view_name": str(view_name).strip(),
                "semantic_view": str(view_name).strip(),
                "version": version,
                "definition_text": definition_text or "",
                "not_deployed_objects": clean_not_deployed,
                "snapshot_id": snapshot_id,
                "deployed_at": now_iso,
                "baseline_source": baseline_source,
                "metrics": clean_metrics,
                "descriptions": clean_descriptions,
                "source_definitions": clean_sources,
                "history": history,
                "updated_at": now_iso,
            }

            now_dt = datetime.now(timezone.utc)
            serialized = json.dumps(baseline_entry, default=str)

            # 1. Save platform-qualified row
            plat_row = session.get(DriftBaselineRow, plat_row_id)
            if plat_row is None:
                session.add(DriftBaselineRow(
                    id=plat_row_id,
                    project_id=str(p_clean),
                    view_name=str(view_name).strip(),
                    version=version,
                    payload_json=serialized,
                    updated_at=now_dt,
                ))
            else:
                plat_row.version = version
                plat_row.payload_json = serialized
                plat_row.updated_at = now_dt

            # 2. Maintain legacy row key for backward compatibility
            legacy_row = session.get(DriftBaselineRow, legacy_row_id)
            if legacy_row is None:
                session.add(DriftBaselineRow(
                    id=legacy_row_id,
                    project_id=str(p_clean),
                    view_name=str(view_name).strip(),
                    version=version,
                    payload_json=serialized,
                    updated_at=now_dt,
                ))
            else:
                legacy_row.version = version
                legacy_row.payload_json = serialized
                legacy_row.updated_at = now_dt

            session.commit()
            logger.info(
                "Saved canonical baseline in DB for project=%s, platform=%s, view=%s, version=%d (%d metrics, %d not deployed)",
                p_clean, platform_norm, view_name, version, len(clean_metrics), len(clean_not_deployed),
            )
            try:
                from semabridge.governance.drift.introspection_cache import invalidate_view_cache
                invalidate_view_cache(platform_norm, view_name)
            except Exception as cache_exc:
                logger.debug("Cache invalidation skipped after saving baseline: %s", cache_exc)
            try:
                from semabridge.governance.ai_impact_service import clear_ai_impact_cache
                clear_ai_impact_cache()
            except Exception as ai_cache_exc:
                logger.debug("AI impact cache invalidation skipped after saving baseline: %s", ai_cache_exc)
            return baseline_entry
    except Exception as exc:
        # A failed save must leave nothing behind: clear any cache entry for that key
        _clear_fallback_cache(p_clean, full_v, platform=platform_norm)
        try:
            from semabridge.governance.drift.introspection_cache import invalidate_view_cache
            invalidate_view_cache(platform_norm, view_name)
        except Exception:
            pass
        try:
            from semabridge.governance.ai_impact_service import clear_ai_impact_cache
            clear_ai_impact_cache()
        except Exception:
            pass
        logger.error(
            "Database save failed for baseline %s (cleared cache, raising): %s",
            plat_row_id, exc,
        )
        raise


def get_active_baseline(
    project_id: str,
    view_name: str,
    platform: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieve the current canonical baseline for a project and view.

    Args:
        project_id: Canonical project identifier.
        view_name: Target view name.
        platform: Optional platform (e.g. SNOWFLAKE, DATABRICKS) to guarantee warehouse isolation.

    Returns:
        The active baseline dictionary, or None if no baseline has been recorded.
        Strictly returns None rather than falling back to local YAML files.
    """
    if not project_id or not view_name:
        return None

    p_clean = _pkey(project_id)
    full_v = _full_view_key(view_name)
    platform_norm = str(platform).upper().strip() if platform else None
    plat_row_id = f"{p_clean}:{platform_norm}:{full_v}" if platform_norm else None
    legacy_row_id = f"{p_clean}:{full_v}"

    try:
        from semabridge.repository.orm.models import DriftBaselineRow
        _ensure_tables()
        with _get_db_session() as session:
            row = None
            if plat_row_id:
                row = session.get(DriftBaselineRow, plat_row_id)
            if row is None:
                row = session.get(DriftBaselineRow, legacy_row_id)
            if row is None:
                candidates = (
                    session.query(DriftBaselineRow)
                    .filter(DriftBaselineRow.project_id == p_clean)
                    .order_by(DriftBaselineRow.version.desc())
                    .all()
                )
                for cand in candidates:
                    cand_full = _full_view_key(cand.view_name)
                    if cand_full == full_v or cand_full.endswith("." + full_v) or full_v.endswith("." + cand_full):
                        if cand.payload_json:
                            cand_data = json.loads(cand.payload_json)
                            if platform_norm and cand_data.get("platform"):
                                if str(cand_data.get("platform")).upper().strip() != platform_norm:
                                    continue
                            row = cand
                            break

            if row is not None and row.payload_json:
                data = json.loads(row.payload_json)
                if data.get("baseline_source") == "snapshot":
                    # Snapshot baselines without executed DDL are invalid phantom drift sources
                    logger.info("Ignoring legacy snapshot baseline %s: DDL deployment baseline required", row.id)
                    return None
                if platform_norm and data.get("platform"):
                    if str(data.get("platform")).upper().strip() != platform_norm:
                        logger.debug("Baseline platform mismatch: requested %s, found %s", platform_norm, data.get("platform"))
                        return None
                return data
    except Exception as exc:
        logger.warning("Could not read baseline %s from database: %s", plat_row_id or legacy_row_id, exc)

    if is_test_memory_backend_enabled():
        return _get_fallback_cache(p_clean, full_v, platform=platform_norm)
    return None


def list_baseline_history(project_id: str, view_name: str) -> List[Dict[str, Any]]:
    """Return historical versions of deploy baselines for the given view."""
    active = get_active_baseline(project_id, view_name)
    if not active:
        return []

    history = list(active.get("history") or [])
    current_summary = {
        "version": active.get("version", 1),
        "deployed_at": active.get("deployed_at"),
        "snapshot_id": active.get("snapshot_id"),
        "metrics_count": len(active.get("metrics") or {}),
        "not_deployed_count": len(active.get("not_deployed_objects") or []),
        "is_active": True,
    }
    return history + [current_summary]


def is_object_not_deployed(baseline: Optional[Dict[str, Any]], object_name: str) -> bool:
    """Check if an object was deliberately dropped / omitted during deploy remediation."""
    if not baseline or not object_name:
        return False
    not_deployed = baseline.get("not_deployed_objects") or []
    return str(object_name).upper().strip() in {str(o).upper().strip() for o in not_deployed}


# -------------------------------------------------------------------------
# In-memory / JSON fallback helpers for disconnected test environments
# -------------------------------------------------------------------------
_memory_baselines: Dict[str, Dict[str, Dict[str, Any]]] = {}
_explicit_test_memory_backend: bool = False


def set_test_memory_backend(enabled: bool) -> None:
    """Explicitly enable or disable the in-memory backend for tests running without any database."""
    global _explicit_test_memory_backend
    _explicit_test_memory_backend = enabled


def is_test_memory_backend_enabled() -> bool:
    return _explicit_test_memory_backend or os.environ.get("SEMABRIDGE_DRIFT_TEST_MEMORY_BACKEND") == "1"


def _clear_fallback_cache(p_clean: str, full_v: str, platform: Optional[str] = None) -> None:
    if p_clean in _memory_baselines:
        _memory_baselines[p_clean].pop(full_v, None)
        if platform:
            _memory_baselines[p_clean].pop(f"{platform}:{full_v}", None)


def _sync_fallback_cache(p_clean: str, full_v: str, entry: Dict[str, Any], platform: Optional[str] = None) -> None:
    _memory_baselines.setdefault(p_clean, {})[full_v] = entry
    if platform:
        _memory_baselines.setdefault(p_clean, {})[f"{platform}:{full_v}"] = entry


def _get_fallback_cache(p_clean: str, full_v: str, platform: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if p_clean in _memory_baselines:
        if platform and f"{platform}:{full_v}" in _memory_baselines[p_clean]:
            return _memory_baselines[p_clean][f"{platform}:{full_v}"]
        if full_v in _memory_baselines[p_clean]:
            data = _memory_baselines[p_clean][full_v]
            if platform and data.get("platform"):
                if str(data.get("platform")).upper().strip() != platform:
                    return None
            return data
        for k, data in _memory_baselines[p_clean].items():
            clean_k = k.split(":", 1)[-1] if ":" in k else k
            if clean_k == full_v or clean_k.endswith("." + full_v) or full_v.endswith("." + clean_k):
                if platform and data.get("platform"):
                    if str(data.get("platform")).upper().strip() != platform:
                        continue
                return data
    return None
