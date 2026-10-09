"""Project mode (single vs multiple models) and per-model naming.

A project's mode is chosen when it is created and saved as ``source.mode``
(``"single"`` or ``"multiple"``). Projects without it are single-mode, so
every existing project keeps exactly its current behaviour and object names.

Multi-mode projects get, once at creation (and for models added later):

  * ``source.model_views`` -- each model's semantic view BASE name (the
    deployed view is ``get_target_deployment_name(base, platform)``). Clashing
    names get a readable ``_2``/``_3`` suffix. Saved in the project config and
    reused by every sync, so a view name never changes between runs.

Per-model physical prefixes (tables, enriched views, MEASURES_ tables) are
derived from those saved view names by :func:`assign_physical_namespaces`,
which guarantees that no prefix is ``<another prefix>_...`` -- the only way
two prefixed names ``<A>_<x>`` and ``<B>_<y>`` from different models could
ever be identical.
"""

from __future__ import annotations

from pathlib import PurePath
from typing import Any, Dict, Iterable, List, Optional

PROJECT_MODE_SINGLE = "single"
PROJECT_MODE_MULTIPLE = "multiple"

# Snowflake's maximum identifier length.
SNOWFLAKE_MAX_IDENTIFIER_LENGTH = 255


def project_mode(source_cfg: Optional[Dict[str, Any]]) -> str:
    """``"multiple"`` only when the project explicitly says so."""
    raw = str((source_cfg or {}).get("mode") or "").strip().lower()
    return PROJECT_MODE_MULTIPLE if raw == PROJECT_MODE_MULTIPLE else PROJECT_MODE_SINGLE


def is_multi_mode(source_cfg: Optional[Dict[str, Any]]) -> bool:
    return project_mode(source_cfg) == PROJECT_MODE_MULTIPLE


def model_entry_key(entry: Any) -> str:
    """The string a ``source.models`` entry is identified by (a PBIX path or
    a model name; dict entries use their path/name)."""
    if isinstance(entry, dict):
        return str(
            entry.get("pbixPath")
            or entry.get("model_path")
            or entry.get("path")
            or entry.get("source_path")
            or entry.get("file_path")
            or entry.get("name")
            or entry.get("view")
            or ""
        ).strip()
    return str(entry or "").strip()


def _view_base_name(source_type: str, entry_key: str) -> str:
    from semabridge.utils.identifiers import IdentifierSanitizer, clean_pbix_model_name

    raw = entry_key
    if str(source_type or "").lower() == "pbix" or entry_key.lower().endswith(".pbix"):
        raw = clean_pbix_model_name(entry_key) or PurePath(entry_key.replace("\\", "/")).stem
    return IdentifierSanitizer().sanitize_table_name(raw) or "MODEL"


def assign_model_view_names(source_cfg: Dict[str, Any]) -> Dict[str, str]:
    """Return ``source.model_views`` for a multi-mode project: existing
    entries are kept exactly; models without a saved name get one, avoiding
    every name already taken (case-insensitive). Deterministic in model order.
    """
    source_type = str(source_cfg.get("type") or "").strip().lower()
    existing = source_cfg.get("model_views") if isinstance(source_cfg.get("model_views"), dict) else {}
    keys = [model_entry_key(entry) for entry in (source_cfg.get("models") or [])]
    keys = [key for key in keys if key]

    result: Dict[str, str] = {}
    taken: set[str] = set()
    for key in keys:
        saved = str(existing.get(key) or "").strip()
        if saved:
            result[key] = saved
            taken.add(saved.upper())
    for key in keys:
        if key in result:
            continue
        base = _view_base_name(source_type, key)
        candidate, n = base, 1
        while candidate.upper() in taken:
            n += 1
            candidate = f"{base}_{n}"
        result[key] = candidate
        taken.add(candidate.upper())
    return result


def saved_view_name_for(source_cfg: Dict[str, Any], *references: Any) -> Optional[str]:
    """The saved view base name for a job, matched by any of its references
    (the source.models entry, its file path, its label)."""
    views = source_cfg.get("model_views") if isinstance(source_cfg.get("model_views"), dict) else {}
    if not views:
        return None

    def _norm(value: Any) -> str:
        text = str(value or "").strip().replace("\\", "/")
        return text.lower()

    by_key = {_norm(key): value for key, value in views.items()}
    for ref in references:
        hit = by_key.get(_norm(ref))
        if hit:
            return str(hit)
    return None


def assign_physical_namespaces(view_names: Iterable[str]) -> List[str]:
    """Readable, unique physical prefixes for the given models (in order).

    Guarantees: all distinct, none longer than the configured maximum, and
    none of them is ``<another prefix>_...`` -- so ``<A>_<anything>`` can never
    equal ``<B>_<anything>`` for two different models.

    Conflicts are resolved by renaming the SHORTER prefix of a prefix-related
    pair (``A`` vs ``A_B`` -> ``A2``), and the later of two equal prefixes,
    with a readable number appended directly (``SALES2``, never a hash).
    Renaming the longer one could never help: ``A_B2`` still starts with
    ``A_``. Deterministic for a given input order, and always terminates
    (each rename strictly increases one counter over a finite set).
    """
    from semabridge.utils.name_translator import build_physical_namespace

    bases = [build_physical_namespace(str(name or "model")) for name in view_names]
    names = list(bases)
    counters = [1] * len(names)

    def _rename(index: int) -> None:
        counters[index] += 1
        names[index] = f"{bases[index]}{counters[index]}"

    changed = True
    while changed:
        changed = False
        for i, j in ((i, j) for i in range(len(names)) for j in range(len(names)) if i != j):
            left, right = names[i].upper(), names[j].upper()
            if left == right and i > j:
                _rename(i)  # later duplicate
                changed = True
            elif right.startswith(f"{left}_"):
                _rename(i)  # i is the shorter, prefix-holding name
                changed = True
    return names
