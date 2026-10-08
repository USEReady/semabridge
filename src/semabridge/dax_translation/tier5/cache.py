"""Persistent, validation-gated cache for Tier-5 (LLM) translation results.

Built after a real production incident (see connectors/type_safety_validator.py's
module docstring and tier5/service.py's detect_nested_aggregate wiring): a
rolling-window metric had translated successfully via Tier 5 in an earlier
sync, then failed on a LATER sync of an equivalent DAX shape because the LLM
produced a structurally different (and this time invalid) SQL string for the
exact same input. Nothing in the pipeline remembered "this exact DAX shape,
for this exact schema, already produced a validated-good translation" --
every sync re-asked the LLM fresh, with no memory of past success, which is
exactly what makes this class of non-determinism possible.

An existing but UNUSED cache class already lived in this codebase
(converter/dax_engine.py's DaxTranslationCache, backed by
.dax_translation_cache.jsonl) -- investigated and found unsuitable to revive
as-is: its key is only hash(dax + table_alias), with no dialect and no
schema-shape component, so two requests with the same DAX+alias but a
DIFFERENT physical column set (e.g. two projects built from different PBIX
sources that happen to share a table alias) would collide and reuse an
invalid translation. It also has no re-validation step on read -- a `put()`
result is trusted forever, even across a future change to what "valid" means.
It is tied to the legacy Pipeline-A TranslationStrategy enum, not the
TranslationRequest/TranslationResult types every current pipeline (A/B/C)
now funnels through via Tier5Service. This module is a new, small,
purpose-built equivalent instead -- same on-disk JSONL shape/spirit, correct
key, and read-time re-validation.

Key design points, all driven by the user's requirements for this fix:
  - The cache key is purely (normalized DAX text, dialect, schema
    signature) -- never a metric name, project id, or model name. The
    schema signature is scoped to exactly the parts of a request that
    determine whether a previously-cached SQL string is safe to reuse
    verbatim: the table alias and the physical column shape it was
    rendered against (table_alias, dataset_aliases, dataset_col_lookup).
    Two unrelated projects/models that happen to share an identical DAX
    expression AND an identical physical schema shape hit the same entry;
    a schema difference (a renamed column, a different alias) never
    collides.
  - `put()` is only ever called by Tier5Service after a candidate has
    already passed the FULL repair/validation pipeline (see
    tier5/service.py's `_repair_and_validate`, which now includes
    detect_nested_aggregate alongside every pre-existing check).
  - A cache HIT is never blindly trusted: the caller re-runs that exact
    same `_repair_and_validate` pipeline on the cached SQL before using it.
    If a validation rule was added or tightened after an entry was
    written (e.g. detect_nested_aggregate itself, added after this cache
    existed), a stale entry fails re-validation and is treated as a MISS --
    the caller falls through to a live LLM call exactly as if nothing had
    ever been cached, and (assuming the fresh candidate validates) the
    stale entry is overwritten with a corrected one.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from semabridge.utils.logger import get_logger
from semabridge.dax_translation.types import TranslationRequest

logger = get_logger(__name__)

# Same "app home" location every other piece of run-scoped/cross-run
# semabridge state already lives in (see utils/logger.py's ~/.semabridge/
# log and config paths) -- deliberately NOT inside the git-tracked repo
# working directory, unlike the old, unused .dax_translation_cache.jsonl.
_DEFAULT_CACHE_PATH = Path.home() / ".semabridge" / "tier5_translation_cache.jsonl"

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_dax(dax: str) -> str:
    """Collapse incidental whitespace differences so two DAX strings that
    differ only in formatting (extra spaces/newlines/indentation) hash
    identically -- they are the same expression shape."""
    return _WHITESPACE_RE.sub(" ", dax or "").strip()


def _schema_signature(request: TranslationRequest) -> str:
    """A stable signature over exactly the parts of a TranslationRequest
    that determine whether a previously-cached SQL string is safe to reuse
    verbatim for THIS request.

    Deliberately excludes dataset_col_types, anchor_flag_map, metric_name,
    and metrics_context: none of those change what identifiers the
    rendered SQL references, only how the LLM was prompted to get there --
    reusing a previously-validated result regardless of those differences
    is exactly the point of this cache.
    """
    payload = {
        "table_alias": request.table_alias,
        "dataset_aliases": sorted((request.dataset_aliases or {}).items()),
        "dataset_col_lookup": sorted(
            (name, sorted(cols)) for name, cols in (request.dataset_col_lookup or {}).items()
        ),
    }
    return json.dumps(payload, sort_keys=True, default=str)


def cache_key(request: TranslationRequest) -> str:
    """Purely content-driven: (normalized DAX + dialect + schema shape).
    Never a metric name, project id, or model name -- the same DAX shape
    appearing in two unrelated models with the same physical schema shape
    hashes to the same key."""
    combined = "|".join([
        _normalize_dax(request.dax),
        request.dialect.value,
        _schema_signature(request),
    ])
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


class Tier5TranslationCache:
    """Maps a cache_key() -> a previously-validated Tier-5 translation
    result, optionally persisted to a JSONL file so it survives across
    separate sync runs.

    With no `cache_file` given, this is a plain in-memory dict scoped to
    this instance's lifetime only -- no disk I/O at all. This is the
    default every Tier5Service() construction gets unless a call site
    explicitly opts into persistence (see default_persistent_cache()
    below), which keeps every existing test that constructs a fresh
    Tier5Service() per test case fully isolated, with zero cross-test
    state leakage through a shared file.
    """

    def __init__(self, cache_file: Optional[Path] = None) -> None:
        self.cache_file = Path(cache_file) if cache_file is not None else None
        self._entries: Dict[str, Dict[str, Any]] = {}
        if self.cache_file is not None:
            self._load()

    def _load(self) -> None:
        if not self.cache_file.exists():
            return
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    key = data.pop("key", None)
                    if key:
                        self._entries[key] = data
            logger.debug(
                "Tier5TranslationCache: loaded %d entries from %s",
                len(self._entries), self.cache_file,
            )
        except Exception as exc:
            logger.warning("Tier5TranslationCache: failed to load %s: %s", self.cache_file, exc)

    def get(self, request: TranslationRequest) -> Optional[Dict[str, Any]]:
        return self._entries.get(cache_key(request))

    def put(self, request: TranslationRequest, result: Any) -> None:
        """`result` is a TranslationResult -- typed loosely here to avoid a
        circular import with types.py; only the fields read back by get()'s
        caller are persisted."""
        key = cache_key(request)
        entry = {
            "sql": result.sql,
            "provider": result.provider,
            "translation_provider_confidence": result.translation_provider_confidence,
            "llm_self_reported_confidence": result.llm_self_reported_confidence,
        }
        self._entries[key] = entry
        if self.cache_file is not None:
            self._append_to_disk(key, entry)

    def _append_to_disk(self, key: str, entry: Dict[str, Any]) -> None:
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, "a", encoding="utf-8") as f:
                f.write(json.dumps({"key": key, **entry}) + "\n")
        except Exception as exc:
            logger.warning(
                "Tier5TranslationCache: failed to persist entry to %s: %s",
                self.cache_file, exc,
            )


_default_cache_lock = threading.Lock()
_default_cache_instance: Optional[Tier5TranslationCache] = None


def reset_default_persistent_cache_for_tests(cache: Optional[Tier5TranslationCache] = None) -> None:
    """Test-only hook: replace the process-wide default_persistent_cache()
    singleton, or drop it back to unset (None -> lazily rebuilt from
    _DEFAULT_CACHE_PATH on next use).

    Production code never calls this. It exists because every real call
    site (DaxTranslationService, converter/dax_translator.py,
    connectors/databricks_publisher.py) shares ONE process-wide cache
    instance by design -- correct for a real run, wrong for a test suite,
    where two unrelated tests can legitimately mock DIFFERENT LLM
    responses for the SAME DAX+dialect+schema shape (nothing about that
    triple is scoped to a test name). Without resetting this between
    tests, one test's mocked "success" response can silently satisfy a
    later, unrelated test's cache lookup instead of that test's own mock
    ever being consulted. See Tests/conftest.py's
    _isolate_tier5_translation_cache fixture, which calls this before
    every test with a fresh in-memory-only instance.
    """
    global _default_cache_instance
    with _default_cache_lock:
        _default_cache_instance = cache


def default_persistent_cache() -> Tier5TranslationCache:
    """The one process-wide, disk-backed cache instance every REAL
    translation call site (DaxTranslationService, and the two legacy
    direct-Tier5Service() call sites in converter/dax_translator.py and
    connectors/databricks_publisher.py) shares, so a validated translation
    from one pipeline/project is available to every other pipeline/project
    with the same DAX+dialect+schema shape -- not just within one run.

    Deliberately NOT the default Tier5Service() gets when constructed with
    no explicit `cache=`; see Tier5TranslationCache's docstring for why
    that matters for test isolation.
    """
    global _default_cache_instance
    with _default_cache_lock:
        if _default_cache_instance is None:
            _default_cache_instance = Tier5TranslationCache(cache_file=_DEFAULT_CACHE_PATH)
        return _default_cache_instance
