"""Shared validation for multi-PBIX source configuration.

Applied at both live request boundaries that can carry a `source.models` /
`selected_sources` list of PBIX files before that list ever reaches
``_build_sync_jobs()``:

  * ``CreateProjectRequest`` (``POST /api/projects``)
  * ``DryRunRequest`` (``POST /api/projects/{project_id}/dry-run``)

``_build_sync_jobs()`` itself is intentionally left untouched. It already
resolves a literal, fully-qualified file path via its exact-match branch
before ever falling back to the `Path(base_dir).glob(...)` heuristic (see
``sync_execution_service.py`` lines 224-234). The multi-PBIX feature relies on
that existing branch by always supplying full absolute paths in
``source.models`` — so this module's job is purely to reject bad input
*before* it reaches that pipeline, with a clear 400, instead of letting a
malformed batch fail confusingly deep inside a background job.

This module is a no-op for anything other than a non-empty ``models`` list on
a ``type: pbix`` source — it must never affect the existing fabric/snowflake
``source.models`` usage (there, entries are model/table *names*, not file
paths), nor the existing single-file ``source.pbix_path`` flow.
"""
from __future__ import annotations

from typing import List, Optional

from semabridge.domain.exceptions import ValidationError

# Kept in sync with semabridge.api.services.sync_execution_service.MAX_BATCH_MODELS,
# which is the hard cap `_build_sync_jobs()` enforces on the resolved job list.
# Duplicated (not imported) to avoid an api.services -> api.services import cycle
# risk as sync_execution_service grows; both values are covered by a regression
# test asserting they stay equal.
MAX_PBIX_FILES = 10


def validate_pbix_model_list(models: Optional[List[str]], *, source_type: str) -> None:
    """Validate a `source.models` list intended for a multi-PBIX source.

    No-op unless ``source_type == "pbix"`` and ``models`` is a non-empty list —
    callers should call this unconditionally with whatever they have; it only
    activates for the case it's designed to guard.

    Raises:
        ValidationError: on anything that would otherwise reach
            ``_build_sync_jobs()`` and fail confusingly (or silently pick the
            wrong file via the glob fallback) deep inside a background job.
    """
    if str(source_type or "").strip().lower() != "pbix":
        return
    if not models:
        return

    if not isinstance(models, list):
        raise ValidationError("source.models must be a list of PBIX file paths.")

    if len(models) > MAX_PBIX_FILES:
        raise ValidationError(
            f"Up to {MAX_PBIX_FILES} PBIX files are supported per project; "
            f"{len(models)} were provided."
        )

    seen = set()
    for entry in models:
        if not isinstance(entry, str) or not entry.strip():
            raise ValidationError("Each entry in source.models must be a non-empty file path.")
        path = entry.strip()
        if not path.lower().endswith(".pbix"):
            raise ValidationError(
                f"'{path}' does not look like a .pbix file path. Multi-file PBIX "
                "projects require a full path to each .pbix file, not a model name."
            )
        if path in seen:
            raise ValidationError(f"Duplicate file selected: '{path}'.")
        seen.add(path)


# Source types a multi-model dry-run job can fan out over (one job file per
# entry in selected_sources). Each entry is a full .pbix path for "pbix" and a
# semantic model name for "fabric".
MULTI_MODEL_SOURCE_TYPES = ("pbix", "fabric")
MAX_DRY_RUN_MODELS = MAX_PBIX_FILES


def dedupe_sources_case_insensitive(sources: Optional[List[str]]) -> List[str]:
    """Strip blanks and drop case-insensitive duplicates, preserving the first
    occurrence's spelling and the original order."""
    out: List[str] = []
    seen = set()
    for entry in sources or []:
        text = str(entry or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def normalize_dry_run_sources(sources: Optional[List[str]], *, source_type: str) -> List[str]:
    """Validate + normalize the ``selected_sources`` list of a multi-model
    dry-run job. Generic over source types: every entry becomes exactly one
    independently-run job file.

    - entries are stripped and de-duplicated case-insensitively;
    - at most ``MAX_DRY_RUN_MODELS`` entries;
    - pbix: delegated to ``validate_pbix_model_list`` (each entry must be a
      ``.pbix`` path);
    - fabric: an empty list is an error (there is no implicit "whole
      workspace" dry run).

    Raises:
        ValidationError: with a client-facing message.
    """
    stype = str(source_type or "").strip().lower()
    if sources is not None and not isinstance(sources, list):
        raise ValidationError("selected_sources must be a list.")
    for entry in sources or []:
        if not isinstance(entry, str):
            raise ValidationError("Each entry in selected_sources must be a string.")
    deduped = dedupe_sources_case_insensitive(sources)

    if len(deduped) > MAX_DRY_RUN_MODELS:
        noun = "PBIX files" if stype == "pbix" else "models"
        raise ValidationError(
            f"Up to {MAX_DRY_RUN_MODELS} {noun} are supported per dry run; "
            f"{len(deduped)} were provided."
        )
    if stype == "pbix":
        validate_pbix_model_list(deduped, source_type="pbix")
    elif stype == "fabric":
        if not deduped:
            raise ValidationError(
                "Select at least one Fabric semantic model to dry run "
                "(selected_sources was empty)."
            )
    return deduped


SUPPORTED_SOURCE_TYPES = ("pbix", "fabric", "snowflake")


def get_supported_source_types() -> tuple[str, ...]:
    from semabridge.core.feature_flags import is_pbi_desktop_source_enabled

    if is_pbi_desktop_source_enabled():
        return ("pbix", "fabric", "snowflake", "pbi_desktop")
    return ("pbix", "fabric", "snowflake")


def validate_pbi_desktop_source_config(source: Optional[dict]) -> None:
    """Validate a source config for Power BI Desktop source.

    Rules:
    - source.type == "pbi_desktop"
    - source.models must be a non-empty list of [{file_path, display_name}] (or strings)
    - At least one model
    - Every file_path must be an absolute .pbix path
    - No duplicate file paths
    - Never save port, PID, database name (reject if present)
    """
    import os
    from pathlib import Path

    if not isinstance(source, dict):
        raise ValidationError("Source configuration must be a dictionary.")

    stype = str(source.get("type") or "").strip().lower()
    if stype != "pbi_desktop":
        return

    from semabridge.core.feature_flags import is_pbi_desktop_source_enabled

    if not is_pbi_desktop_source_enabled():
        raise ValidationError(
            "Power BI Desktop source is disabled (feature flag 'pbi_desktop_source' is off)."
        )

    # Dynamic session properties that change every session must never be saved
    for forbidden in ("port", "pid", "msmdsrv_pid", "database", "database_name", "server"):
        if forbidden in source:
            raise ValidationError(
                f"Dynamic session field '{forbidden}' must not be saved in project config."
            )

    models = source.get("models")
    if not models or not isinstance(models, list):
        single_model = source.get("model")
        if single_model and isinstance(single_model, (str, dict)) and str(single_model).strip() not in ("", "*", "[object Object]"):
            models = [single_model]
            source["models"] = models
            source.pop("model", None)
        else:
            raise ValidationError(
                "At least one Power BI Desktop model must be selected (source.models)."
            )
    else:
        # Clean singular model if present alongside models list
        source.pop("model", None)

    seen_keys = set()
    for entry in models:
        raw_path = ""
        display_name = ""
        if isinstance(entry, dict):
            raw_path = str(entry.get("file_path") or entry.get("path") or "").strip()
            display_name = str(entry.get("display_name") or entry.get("name") or "").strip()
        elif isinstance(entry, str):
            s = entry.strip()
            if s.lower().endswith(".pbix") or "\\" in s or "/" in s:
                raw_path = s
                display_name = Path(s).stem
            else:
                display_name = s
        else:
            raise ValidationError(
                "Each model entry must be a dictionary with 'display_name' (and optional 'file_path') or a string."
            )

        if not display_name and raw_path:
            display_name = Path(raw_path).stem

        if not display_name:
            raise ValidationError("Model entry must specify a 'display_name' or 'file_path'.")

        if display_name.strip().lower() in ("unsaved new file", "untitled", "untitled.pbix"):
            raise ValidationError(
                "Cannot save 'Unsaved new file' as a project source. Please save the file in Power BI Desktop first."
            )

        if raw_path:
            p = Path(raw_path)
            if p.suffix.lower() != ".pbix":
                raise ValidationError(f"'{raw_path}' does not look like a .pbix file path.")

            if not p.is_absolute():
                raise ValidationError(
                    f"'{raw_path}' is not an absolute path. Full absolute .pbix path is required."
                )

            try:
                norm_path = os.path.normcase(str(p.resolve()))
            except Exception:
                norm_path = os.path.normcase(raw_path)
            dedupe_key = f"path:{norm_path}"
        else:
            dedupe_key = f"name:{display_name.casefold()}"

        if dedupe_key in seen_keys:
            ident = raw_path or display_name
            raise ValidationError(f"Duplicate model selected: '{ident}'.")
        seen_keys.add(dedupe_key)


def require_source_type(source_cfg: Optional[dict], *, where: str = "project") -> str:
    """The project's ``source.type``, required at create/save.

    There is no default source type: a missing or unknown type is a
    validation error naming the field, instead of silently becoming
    ``fabric`` and failing later with confusing auth/workspace errors.
    """
    stype = str((source_cfg or {}).get("type") or "").strip().lower()
    allowed = get_supported_source_types()
    if not stype:
        raise ValidationError(
            f"The {where} has no source type: set source.type to one of {', '.join(allowed)}."
        )
    if stype not in allowed:
        raise ValidationError(
            f"Unsupported source.type '{stype}' for the {where}; use one of {', '.join(allowed)}."
        )
    return stype
