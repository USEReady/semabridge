"""Ontology review for the new-project wizard (runs BEFORE the dry run).

Reads the selected source models into OSI (no target calls, no SML/DDL), runs
the quality analysis against the federated ontology, and turns the reviewer's
decisions into a saved domain.

Source support:
* ``pbix``: each selected .pbix is read locally (same connector + converter
  the dry run uses), so the review works before any dry run;
* any source for an EXISTING project: the project's latest OSI snapshot.
Other sources on a brand-new project raise ``UnsupportedSourceError`` -- their
extraction needs the per-user connector setup that only the dry-run job has.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_CACHE_TTL_S = 60 * 60
_CACHE_MAX = 20
_cache: "OrderedDict[str, Tuple[float, List[Any]]]" = OrderedDict()
_cache_lock = threading.Lock()


class UnsupportedSourceError(ValueError):
    """The source type can't be read before the dry run (yet)."""


def _cache_put(models: List[Any]) -> str:
    key = uuid.uuid4().hex
    with _cache_lock:
        _cache[key] = (time.time(), models)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return key


def cache_get(key: Optional[str]) -> Optional[List[Any]]:
    if not key:
        return None
    with _cache_lock:
        hit = _cache.get(key)
        if not hit:
            return None
        ts, models = hit
        if time.time() - ts > _CACHE_TTL_S:
            _cache.pop(key, None)
            return None
        return models


def _read_pbix(path_str: str) -> Any:
    from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
    from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
    from semabridge.utils.identifiers import clean_pbix_model_name

    path = Path(str(path_str).strip().strip('"').strip("'")).expanduser()
    if path.suffix.lower() != ".pbix":
        raise ValueError("not a .pbix file")
    if not path.is_file():
        raise ValueError("file not found on the server; upload it again")

    connector = LocalPBIXConnector({"pbix_path": str(path)})
    discovered = connector.discover()
    tmsl = discovered.get("raw_tmsl") or connector.extract()
    if not tmsl:
        raise ValueError("could not read the data model")
    name = clean_pbix_model_name(str(path)) or path.stem
    return TMSLToOSIConverter().to_osi(
        {
            "tmsl": tmsl,
            "workspace_id": "local",
            "dataset_id": name,
            "display_name": name,
            "field_aliases": discovered.get("field_aliases", []),
            "source_file": str(path),
        }
    )


async def read_source_models(
    source_config: Dict[str, Any],
    selected_sources: List[str],
    project_id: Optional[str] = None,
) -> Tuple[List[Any], List[Dict[str, str]]]:
    """Return (OSI models, per-source errors)."""
    import asyncio

    stype = str((source_config or {}).get("type") or "").strip().lower()
    models: List[Any] = []
    errors: List[Dict[str, str]] = []

    if stype == "pbix":
        # Single-file wizards send model names in selected_sources and the file
        # in source_config.pbix_path; multi-file wizards send the file paths.
        paths = [str(p) for p in (selected_sources or []) if str(p).strip().lower().endswith(".pbix")]
        if not paths and source_config.get("pbix_path"):
            paths = [str(source_config["pbix_path"])]
        if not paths:
            raise UnsupportedSourceError("Select at least one .pbix file to review its measures.")
        # The same file selected twice would report every measure as a duplicate.
        paths = list(OrderedDict((str(Path(p).expanduser()).casefold(), p) for p in paths).values())
        for p in paths:
            try:
                models.append(await asyncio.to_thread(_read_pbix, p))
            except Exception as exc:  # one bad file must not hide the others
                logger.warning("Ontology review: could not read %s: %s", p, exc)
                errors.append({"source": Path(str(p)).name, "error": str(exc)})
        return models, errors

    if project_id and project_id not in ("preview", ""):
        from semabridge.api.routers.export_router import _load_osi_model

        osi = await _load_osi_model(project_id)
        if osi is not None:
            return [osi], errors

    raise UnsupportedSourceError(
        f"Ontology review before the dry run currently supports Power BI files (.pbix). "
        f"For '{stype or 'this source'}', run the dry run first; the ontology can be "
        f"reviewed from the project's Ontology tab afterwards."
    )


def combine_models(models: List[Any], name: str) -> Any:
    """One OSIModel holding every model's datasets/metrics/relationships (first wins by name)."""
    if not models:
        raise ValueError("No source models to combine")
    if len(models) == 1:
        return models[0]

    def merged(attr: str) -> List[Any]:
        out: "OrderedDict[str, Any]" = OrderedDict()
        for model in models:
            for item in getattr(model, attr, None) or []:
                key = str(getattr(item, "unique_name", "") or id(item)).casefold()
                out.setdefault(key, item)
        return list(out.values())

    update: Dict[str, Any] = {
        "unique_name": name,
        "label": name,
        "datasets": merged("datasets"),
        "metrics": merged("metrics"),
        "relationships": merged("relationships"),
    }
    if hasattr(models[0], "dimensions"):
        update["dimensions"] = merged("dimensions")
    return models[0].model_copy(update=update)


def analyze(models: List[Any]) -> Dict[str, Any]:
    from semabridge.ontology.quality import analyze_source_metrics, source_metrics_from_osi
    from semabridge.ontology.store import ontology_store

    metrics = []
    for model in models:
        metrics.extend(source_metrics_from_osi(model))
    return analyze_source_metrics(metrics, ontology_store.get_federated_ontology())


async def run_analysis(
    source_config: Dict[str, Any],
    selected_sources: List[str],
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    models, errors = await read_source_models(source_config, selected_sources, project_id)
    if not models:
        return {"analysis_id": None, "errors": errors, "summary": None}
    result = analyze(models)
    result["analysis_id"] = _cache_put(models)
    result["errors"] = errors
    return result


def build_reviewed_domain(
    models: List[Any],
    *,
    domain_name: str,
    owner: Optional[str],
    merges: List[Dict[str, Any]],
    conflicts: List[Dict[str, Any]],
    descriptions: Dict[str, str],
    existing: Optional[Any] = None,
) -> Tuple[Any, Dict[str, List[str]]]:
    """Build the reviewed domain; with ``existing`` it MERGES into that domain
    (same semantics as a re-sync: user edits kept, BI items refreshed)."""
    from semabridge.ontology.federated_loader import FederatedOntologyLoader
    from semabridge.ontology.quality import apply_review_decisions

    combined = combine_models(models, domain_name)
    draft = FederatedOntologyLoader.from_osi_model(
        model=combined, domain_name=domain_name, existing_ontology=existing
    )
    if owner:
        draft = draft.model_copy(update={"owner": owner})
    return apply_review_decisions(draft, merges=merges, conflicts=conflicts, descriptions=descriptions)
