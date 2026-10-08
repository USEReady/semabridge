from __future__ import annotations

import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Generator, List, Set

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


def get_pbix_deployment_view_name(pbix_path: str, platform: str = "snowflake") -> str:
    """
    Derives the deployment view name for a single PBIX file from its OWN
    filename — never from the project name. This is what gives each file in a
    multi-PBIX project its own independent Snowflake semantic view.

    Reuses two already-proven utilities rather than inventing a new sanitizer:
      1. IdentifierSanitizer.sanitize_table_name() — handles leading digits,
         illegal characters, uppercasing (things get_target_deployment_name's
         own regex-strip doesn't handle, e.g. a filename starting with a digit).
      2. get_target_deployment_name() — the existing display-name -> deployment
         -name translation, applied here to the sanitized filename stem instead
         of a project name.

    Args:
        pbix_path: Full path (or bare filename) to the .pbix file.
        platform: Target platform, passed straight through to
            get_target_deployment_name (default "snowflake").

    Returns:
        The deployment view name, e.g. "C:/Reports/Sales Report.pbix" ->
        "SALES_REPORT_SEMANTIC" (platform="snowflake").
    """
    from semabridge.utils.identifiers import IdentifierSanitizer, clean_pbix_model_name

    stem = clean_pbix_model_name(pbix_path) or Path(pbix_path).stem
    sanitizer = IdentifierSanitizer()
    table_safe = sanitizer.sanitize_table_name(stem)
    return get_target_deployment_name(table_safe, platform)


def resolve_pbix_deployment_base_names(pbix_paths: List[str]) -> Dict[str, str]:
    """Maps each PBIX path in a batch to its own clean, sanitized BASE name
    (the pre-platform-suffix name pbix.py's _convert_pbix_to_sml sets as
    OSIModel/SMLModel.unique_name -- get_target_deployment_name's "_SEMANTIC"
    /"_FB"/"_DBX" suffix is applied later, at DDL-emission time).

    Every file gets its own clean name by default (matching demo_version's
    single-file behavior: no leading hash/prefix). Disambiguation only
    kicks in when two files in THIS batch would otherwise produce the
    identical base name -- e.g. two different reports both saved as
    "Sales.pbix", or two PBIX files whose internal model happens to share a
    display name. The first occurrence (input order) keeps the clean,
    unsuffixed name; later occurrences get a short, readable "_2", "_3", ...
    suffix -- never a long UUID/hash, and never a hard validation error.

    This mirrors the collision-handling shape already used elsewhere in this
    codebase (see dimensions_clause_builder.py's alias _2/_3 fallback): try
    the base name first, only disambiguate when a genuine collision is
    actually detected.
    """
    from semabridge.utils.identifiers import IdentifierSanitizer, clean_pbix_model_name

    sanitizer = IdentifierSanitizer()
    base_names = [
        sanitizer.sanitize_table_name(clean_pbix_model_name(path) or Path(path).stem)
        for path in pbix_paths
    ]

    counts: Dict[str, int] = {}
    for name in base_names:
        counts[name] = counts.get(name, 0) + 1

    occurrence: Dict[str, int] = {}
    resolved: Dict[str, str] = {}
    for path, base_name in zip(pbix_paths, base_names):
        if counts[base_name] == 1:
            resolved[path] = base_name
            continue
        occurrence[base_name] = occurrence.get(base_name, 0) + 1
        n = occurrence[base_name]
        resolved[path] = base_name if n == 1 else f"{base_name}_{n}"
    return resolved


def find_pbix_view_name_collisions(
    pbix_paths: List[str], platform: str = "snowflake"
) -> Dict[str, List[str]]:
    """
    Groups PBIX file paths by the deployment view name they would each produce,
    returning only groups with more than one file — i.e. genuine collisions.

    Read-only diagnostic helper (e.g. for logging/telemetry) — actual
    disambiguation for a batch of PBIX files is resolve_pbix_deployment_base_names(),
    which auto-suffixes colliding names instead of erroring, so this function
    itself no longer gates project creation, dry-run, or deploy requests.

    Args:
        pbix_paths: Full paths to the configured .pbix files.
        platform: Target platform for name derivation (default "snowflake").

    Returns:
        Dict of {view_name: [colliding_paths]} — empty if no collisions.
    """
    by_view_name: Dict[str, List[str]] = {}
    for path in pbix_paths:
        view_name = get_pbix_deployment_view_name(path, platform)
        by_view_name.setdefault(view_name, []).append(path)
    return {name: paths for name, paths in by_view_name.items() if len(paths) > 1}


# Maximum length of a per-model physical namespace prefix. Snowflake allows
# 255-char identifiers, but the prefix is prepended to every table/view name
# of the model, so it is kept short. Longer names are truncated readably (no
# hash); two prefixes that truncate to the same value are disambiguated by the
# caller with make_unique_labels()'s "_2"/"_3" suffixes.
_PHYSICAL_NAMESPACE_MAX_LEN = 40


def build_physical_namespace(name: str) -> str:
    """Return a deterministic, readable, identifier-safe per-model namespace prefix.

    Used ONLY for multi-mode projects (see sync_execution_service's
    _assign_project_mode_naming): every physical object a model's
    deploy creates in the shared target schema (base tables, *_ENRICHED
    views, MEASURES_* tables) is prefixed with this value so two models of
    the same project that contain a table with the same name never share
    or overwrite each other's physical objects. Single-mode projects never
    get a namespace, so their object names are unchanged.
    """
    from semabridge.utils.identifiers import IdentifierSanitizer, clean_pbix_model_name

    raw = str(name or "").strip() or "MODEL"
    stem = clean_pbix_model_name(raw) if raw.lower().endswith(".pbix") else raw
    safe = IdentifierSanitizer().sanitize_table_name(stem) or "MODEL"
    safe = safe.upper()
    if len(safe) > _PHYSICAL_NAMESPACE_MAX_LEN:
        safe = safe[:_PHYSICAL_NAMESPACE_MAX_LEN].rstrip("_") or "MODEL"
    return safe


def make_unique_labels(labels: List[str]) -> List[str]:
    """Suffix duplicate labels ("_2", "_3", ...) so every entry is unique.

    The first occurrence keeps its label unchanged, mirroring
    resolve_pbix_deployment_base_names()'s collision policy.
    """
    seen: Dict[str, int] = {}
    taken = set(labels)
    result: List[str] = []
    for label in labels:
        count = seen.get(label, 0) + 1
        seen[label] = count
        if count == 1:
            result.append(label)
            continue
        n = count
        candidate = f"{label}_{n}"
        while candidate in taken:
            n += 1
            candidate = f"{label}_{n}"
        taken.add(candidate)
        result.append(candidate)
    return result


class BatchViewNameRegistry:
    """Thread-safe claim registry for view base names within ONE sync batch.

    PBIX jobs get their batch-disambiguated names up front (file names are
    known before extraction). Fabric dataset display names are only known
    after each job's own extraction, so jobs of a multi-model Fabric batch
    claim their name here at conversion time: the first claimant keeps the
    clean name, later claimants of the same name get "_2"/"_3" -- the same
    suffix policy resolve_pbix_deployment_base_names() uses.
    """

    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()
        self._claims: Dict[str, str] = {}  # upper-cased name -> owner key

    def claim(self, owner_key: str, base_name: str) -> str:
        base = str(base_name or "").strip() or "model"
        with self._lock:
            n = 1
            candidate = base
            while True:
                owner = self._claims.get(candidate.upper())
                if owner is None or owner == owner_key:
                    self._claims[candidate.upper()] = owner_key
                    return candidate
                n += 1
                candidate = f"{base}_{n}"

    def __getstate__(self):  # pragma: no cover - only hit by process pools
        raise TypeError("BatchViewNameRegistry is thread-scoped and cannot be pickled")
