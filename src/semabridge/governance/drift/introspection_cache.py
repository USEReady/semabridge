"""Time-To-Live (TTL) Introspection Cache for Warehouse Calls.

Caches raw definition text fetched from warehouse platforms to avoid excessive
warehouse queries and rate limits during repeated drift checks.

Key features:
- Configurable TTL (SEMABRIDGE_DRIFT_CACHE_TTL_SECONDS, default: 300s / 5m)
- Configurable maximum size (SEMABRIDGE_DRIFT_CACHE_MAX_SIZE, default: 500 entries)
- Thread-safe in-memory LRU cache
- Cache key: (platform, connection_identity, view_name)
- Force-refresh bypass flag
- Per-view invalidation on successful deployments
- Strictly no hardcoded credentials, accounts, catalogs, or identifiers.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("semabridge.governance.drift.introspection_cache")

_DEFAULT_TTL_SECONDS = 300
_DEFAULT_MAX_SIZE = 500


def get_cache_ttl_seconds() -> int:
    """Return the configured cache TTL in seconds."""
    raw = os.getenv("SEMABRIDGE_DRIFT_CACHE_TTL_SECONDS", "").strip()
    if raw.isdigit():
        val = int(raw)
        if val >= 0:
            return val
    return _DEFAULT_TTL_SECONDS


def get_cache_max_size() -> int:
    """Return the configured maximum cache size."""
    raw = os.getenv("SEMABRIDGE_DRIFT_CACHE_MAX_SIZE", "").strip()
    if raw.isdigit():
        val = int(raw)
        if val > 0:
            return val
    return _DEFAULT_MAX_SIZE


def derive_connection_identity(connection_config: Optional[Dict[str, Any]]) -> str:
    """Derive a safe, anonymized connection identity string from connection config."""
    if not connection_config or not isinstance(connection_config, dict):
        return "default"
    account = str(connection_config.get("account") or "").strip().lower()
    user = str(connection_config.get("user") or "").strip().lower()
    database = str(connection_config.get("database") or "").strip().lower()
    host = str(connection_config.get("server_hostname") or connection_config.get("host") or "").strip().lower()
    catalog = str(connection_config.get("catalog") or "").strip().lower()

    parts = [p for p in (account, user, database, host, catalog) if p]
    if not parts:
        return "default"
    return ":".join(parts)


@dataclass
class CacheEntry:
    definition_text: str
    cached_at: datetime
    platform: str
    view_name: str
    connection_identity: str
    extra: Optional[Dict[str, Any]] = None


class IntrospectionCache:
    """Thread-safe TTL & bounded LRU cache for warehouse view definitions."""

    def __init__(self, ttl_seconds: Optional[int] = None, max_size: Optional[int] = None) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_size = max_size
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds if self._ttl_seconds is not None else get_cache_ttl_seconds()

    @property
    def max_size(self) -> int:
        return self._max_size if self._max_size is not None else get_cache_max_size()

    def _make_key(self, platform: str, connection_identity: str, view_name: str) -> str:
        plat = str(platform or "").strip().upper()
        ident = str(connection_identity or "default").strip().lower()
        view = str(view_name or "").strip().upper()
        return f"{plat}::{ident}::{view}"

    def get(
        self,
        platform: str,
        connection_identity: str,
        view_name: str,
        force_refresh: bool = False,
    ) -> Optional[str]:
        """Retrieve cached definition text if present and not expired."""
        if force_refresh or self.ttl_seconds <= 0:
            return None

        key = self._make_key(platform, connection_identity, view_name)
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None

            now = datetime.now(timezone.utc)
            age = (now - entry.cached_at).total_seconds()
            if age > self.ttl_seconds:
                # Expired
                self._cache.pop(key, None)
                logger.debug("Cache entry expired for %s (age=%.1fs, ttl=%ds)", key, age, self.ttl_seconds)
                return None

            # Move to end (most recently used)
            self._cache.move_to_end(key)
            return entry.definition_text

    def set(
        self,
        platform: str,
        connection_identity: str,
        view_name: str,
        definition_text: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Store definition text into cache with current timestamp, evicting oldest if full."""
        if self.ttl_seconds <= 0 or self.max_size <= 0:
            return

        key = self._make_key(platform, connection_identity, view_name)
        now = datetime.now(timezone.utc)
        entry = CacheEntry(
            definition_text=definition_text,
            cached_at=now,
            platform=str(platform or "").strip().upper(),
            view_name=str(view_name or "").strip(),
            connection_identity=str(connection_identity or "default").strip().lower(),
            extra=extra,
        )

        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = entry

            # Evict oldest if beyond capacity
            while len(self._cache) > self.max_size:
                evicted_key, _ = self._cache.popitem(last=False)
                logger.debug("Evicted oldest cache entry: %s", evicted_key)

    def invalidate(self, platform: str, view_name: str, connection_identity: Optional[str] = None) -> int:
        """Invalidate entries matching platform and view_name.

        If connection_identity is None, invalidates for all identities for that platform and view.
        Returns the number of invalidated entries.
        Strictly prevents cross-platform cache invalidation (Snowflake vs Databricks).
        """
        plat = str(platform or "").strip().upper()
        target_view = str(view_name or "").strip().upper()
        target_clean = target_view.strip('"').strip('`').split(".")[-1]
        target_ident = str(connection_identity).strip().lower() if connection_identity else None

        with self._lock:
            keys_to_remove = []
            for k, entry in self._cache.items():
                entry_view = entry.view_name.strip().upper()
                entry_clean = entry_view.strip('"').strip('`').split(".")[-1]

                # Strict platform matching: platform must match exactly so Snowflake does not invalidate Databricks and vice versa
                if plat and entry.platform != plat:
                    continue

                # View matching: match full qualified view name or tail if target is single-part
                matches_view = (entry_view == target_view or ('.' not in target_view and entry_clean == target_clean))
                if matches_view:
                    if target_ident is None or entry.connection_identity == target_ident:
                        keys_to_remove.append(k)

            for k in keys_to_remove:
                self._cache.pop(k, None)

            if keys_to_remove:
                logger.info("Invalidated %d cache entry/entries for %s view %s", len(keys_to_remove), plat, view_name)
            return len(keys_to_remove)

    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()

    def size(self) -> int:
        """Return number of cached entries."""
        with self._lock:
            return len(self._cache)


# Global singleton instance
_GLOBAL_CACHE = IntrospectionCache()


def get_cached_definition(
    platform: str,
    connection_identity: str,
    view_name: str,
    force_refresh: bool = False,
) -> Optional[str]:
    """Module-level helper to get cached definition text."""
    return _GLOBAL_CACHE.get(platform, connection_identity, view_name, force_refresh=force_refresh)


def set_cached_definition(
    platform: str,
    connection_identity: str,
    view_name: str,
    definition_text: str,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Module-level helper to set cached definition text."""
    _GLOBAL_CACHE.set(platform, connection_identity, view_name, definition_text, extra=extra)


def invalidate_cached_definition(
    platform: str,
    view_name: str,
    connection_identity: Optional[str] = None,
) -> int:
    """Module-level helper to invalidate cached definitions on deploy."""
    return _GLOBAL_CACHE.invalidate(platform, view_name, connection_identity=connection_identity)


invalidate_view_cache = invalidate_cached_definition


def clear_cache() -> None:
    """Module-level helper to clear the cache."""
    _GLOBAL_CACHE.clear()
