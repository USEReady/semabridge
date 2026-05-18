"""Boundary translator for Fabric TMSL payloads <-> internal SML model."""

from __future__ import annotations

from typing import Any, Optional

from semabridge.converter.tmsl_to_sml import TMDLTransformer
from semabridge.sml.models import SMLModel


def translate_tmsl_to_internal_sml(
    raw_tmsl_payload: dict[str, Any],
    *,
    workspace_id: str,
    dataset_id: str,
    row_counts: Optional[dict[str, int]] = None,
) -> SMLModel:
    """Translate decoded Fabric model.bim payload into internal SML model."""
    transformer = TMDLTransformer()
    return transformer.transform(
        raw_tmsl_payload,
        workspace_id=workspace_id,
        dataset_id=dataset_id,
        row_counts=row_counts or {},
    )

