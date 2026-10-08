from __future__ import annotations

import re
import hashlib
import threading
from contextlib import contextmanager
from typing import Dict, Generator, List, Optional, Set

def get_target_deployment_name(display_name: str, platform: str = "") -> str:
    """
    Translates a human-readable display name into a strict, capitalized,
    platform-specific deployment name.
    
    Args:
        display_name: The semantic project name from the user config.
        platform: The target platform (e.g., 'snowflake', 'fabric').
        
    Returns:
        A deterministic, uppercase string safe for deployment.
    """
    # Replace spaces with underscores
    sanitized = display_name.replace(' ', '_')
    # Remove any non-alphanumeric/underscore characters
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '', sanitized)
    # Capitalize
    capitalized = sanitized.upper()
    
    # Apply platform-specific suffixes if provided, to ensure multiple
    # models deployed from the same project get distinct names
    if platform:
        p_upper = platform.upper()
        if p_upper == "SNOWFLAKE":
            return f"{capitalized}_SEMANTIC"
        elif p_upper == "FABRIC":
            return f"{capitalized}_FB"
        elif p_upper == "DATABRICKS":
            return f"{capitalized}_DBX"
        else:
            return f"{capitalized}_{p_upper}"
            
    return capitalized


def get_model_scoped_object_name(base_object_name: str, model_id: str, suffix: str = "ENRICHED") -> str:
    """
    Generates a stable, model-scoped Snowflake object name.
    Ensures that two models sharing a fact table name (e.g. SALESFACT) produce
    distinct enriched view names (e.g. SALESFACT_A83F1_ENRICHED) without colliding.
    Respects Snowflake's 255-character identifier limit.
    """
    raw_base = re.sub(r'[^a-zA-Z0-9_]', '_', str(base_object_name or "OBJECT")).strip('_').upper()
    model_hash = hashlib.sha256(str(model_id or "default").encode('utf-8')).hexdigest()[:6].upper()
    suf = str(suffix or "").strip('_').upper()
    suffix_part = f"_{suf}" if suf else ""
    
    # Max length: 255 chars for Snowflake identifiers
    max_base_len = 255 - len(model_hash) - len(suffix_part) - 2
    truncated_base = raw_base[:max_base_len]
    return f"{truncated_base}_{model_hash}{suffix_part}"


def find_target_view_collisions(
    model_identifiers: List[str], platform: str = "snowflake"
) -> Dict[str, List[str]]:
    """
    Groups model identifiers by the deployment view name they would produce,
    returning only groups with collisions.
    """
    by_view_name: Dict[str, List[str]] = {}
    for ident in model_identifiers:
        view_name = get_target_deployment_name(ident, platform)
        by_view_name.setdefault(view_name, []).append(ident)
    return {name: idents for name, idents in by_view_name.items() if len(idents) > 1}


def validate_no_target_view_collisions(
    model_identifiers: List[str], platform: str = "snowflake"
) -> None:
    """
    Raises ValidationError if any two models would deploy to the same view name.
    """
    from semabridge.domain.exceptions import ValidationError

    collisions = find_target_view_collisions(model_identifiers, platform)
    if not collisions:
        return

    parts = []
    for view_name, idents in collisions.items():
        formatted_idents = ", ".join(f"'{i}'" for i in idents)
        parts.append(f"Models {formatted_idents} would all deploy to target view name '{view_name}'")
    detail = "; ".join(parts)
    raise ValidationError(f"Target view name collision detected: {detail}")


class TargetLockManager:
    """
    Thread-safe per-target view lock manager to prevent concurrent deployments
    to the same target semantic view across requests and threads.
    """

    def __init__(self) -> None:
        self._global_lock = threading.Lock()
        self._active_target_locks: Dict[str, threading.Lock] = {}
        self._acquired_views: Set[str] = set()

    def acquire(self, target_view_name: str) -> bool:
        key = str(target_view_name or "").strip().upper()
        if not key:
            return True
        with self._global_lock:
            if key in self._acquired_views:
                return False
            lock = self._active_target_locks.setdefault(key, threading.Lock())
            if lock.acquire(blocking=False):
                self._acquired_views.add(key)
                return True
            return False

    def release(self, target_view_name: str) -> None:
        key = str(target_view_name or "").strip().upper()
        if not key:
            return
        with self._global_lock:
            if key in self._acquired_views:
                self._acquired_views.remove(key)
                lock = self._active_target_locks.get(key)
                if lock and lock.locked():
                    try:
                        lock.release()
                    except RuntimeError:
                        pass

    @contextmanager
    def hold(self, target_view_name: str) -> Generator[bool, None, None]:
        acquired = self.acquire(target_view_name)
        try:
            yield acquired
        finally:
            if acquired:
                self.release(target_view_name)


# Global target lock manager instance
target_lock_manager = TargetLockManager()

