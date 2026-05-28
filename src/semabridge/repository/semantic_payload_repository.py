"""Repository helpers for persisted official semantic payloads."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Optional, Sequence

from sqlalchemy.orm import Session

from semabridge.repository.orm.models import ParsedSemanticPayload, SourceArtifact
from semabridge.sml.models import SMLModel
from semabridge.transformers.official_payload import smlmodel_to_official_payload


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def normalize_official_payload(content: Dict[str, Any]) -> Dict[str, Any]:
    """Convert legacy stored artifact content to the official payload shape.

    The helper accepts either an already-official payload or legacy SML JSON.
    Unsupported shapes are returned as-is so callers can decide whether to
    skip or persist them.
    """
    content = _as_dict(content)
    if not content:
        return {}

    if "datasets" in content and "metrics" in content and (
        "model_name" in content or "unique_name" in content
    ):
        payload = dict(content)
        payload.setdefault("model_name", payload.get("unique_name"))
        return payload

    if "unique_name" in content and "datasets" in content:
        try:
            sml = SMLModel.model_validate(content)
        except Exception:
            return content
        return smlmodel_to_official_payload(sml, target_platform=str(content.get("target_platform") or "snowflake"))

    if isinstance(content.get("semantic_state"), dict):
        return normalize_official_payload(content["semantic_state"])

    return content


def backfill_parsed_semantic_payloads(
    session: Session,
    *,
    source_types: Optional[Sequence[str]] = None,
    limit: Optional[int] = None,
    update_existing: bool = True,
) -> int:
    """Backfill official semantic payload rows from legacy source artifacts.

    Returns the number of payload rows inserted or updated.
    """
    query = session.query(SourceArtifact).order_by(SourceArtifact.created_at.asc())
    if source_types:
        normalized = {str(item).strip().casefold() for item in source_types if str(item).strip()}
        if normalized:
            query = query.filter(
                SourceArtifact.source_type.in_(sorted(normalized))
            )
    if limit is not None:
        query = query.limit(int(limit))

    touched = 0
    for artifact in query.all():
        try:
            content = json.loads(artifact.content_json or "{}")
        except Exception:
            continue

        payload = normalize_official_payload(content)
        model_name = str(payload.get("model_name") or payload.get("unique_name") or "").strip()
        if not model_name:
            continue

        version = payload.get("version")
        source_platform = payload.get("source_platform")
        target_platform = payload.get("target_platform")

        existing = (
            session.query(ParsedSemanticPayload)
            .filter(ParsedSemanticPayload.model_name == model_name)
            .filter(ParsedSemanticPayload.version == version)
            .filter(ParsedSemanticPayload.source_platform == source_platform)
            .filter(ParsedSemanticPayload.target_platform == target_platform)
            .first()
        )

        if existing is None:
            row = ParsedSemanticPayload(
                model_name=model_name,
                version=version,
                source_platform=source_platform,
                target_platform=target_platform,
            )
            session.add(row)
            row.payload = payload
        elif update_existing:
            existing.payload = payload

        touched += 1

    session.commit()
    return touched
