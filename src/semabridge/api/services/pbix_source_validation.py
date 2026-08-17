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
