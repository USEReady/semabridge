"""
Bulk Collision Resolution API
==============================
Exposes a single endpoint that pre-computes deterministic hash-based target
attribute names for all colliding source fields in a mapping request.

The response is a suggestion list suitable for frontend review — no changes
are applied automatically; the user must confirm via the UI.

Design principles:
  - Stateless: all inputs come from the request body; no DB writes here.
  - Deterministic: calling this endpoint twice with the same payload produces
    identical output (SHA-256 is stable).
  - Snowflake-safe: all generated target names are UPPERCASE and unquoted.
"""

from __future__ import annotations

import hashlib
from typing import List

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/mapping", tags=["Mapping"])

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class CollisionField(BaseModel):
    """A single source field that has a naming collision."""
    entity_name: str   # e.g. "Account"
    field_name: str    # e.g. "REGION"
    current_target: str  # e.g. "REGION"


class ResolvedField(BaseModel):
    """A single resolved suggestion, ready for frontend review."""
    entity_name: str
    field_name: str
    current_target: str
    suggested_target: str   # e.g. "REGION_A1B2"
    hash_suffix: str        # e.g. "A1B2" (for audit display)
    status: str = "suggestion"  # UI state: 'suggestion' | 'manual' | 'approved'


class BulkResolveRequest(BaseModel):
    """Payload for bulk collision resolution."""
    collisions: List[CollisionField]


class BulkResolveResponse(BaseModel):
    """Response containing all resolved suggestions."""
    resolved: List[ResolvedField]
    total: int


# ---------------------------------------------------------------------------
# Hash utility
# ---------------------------------------------------------------------------

def _deterministic_hash(entity_name: str, field_name: str) -> str:
    """Return a 4-char uppercase SHA-256 hash seeded on entity + field name.

    Identical to ``SemanticViewBuilder._generate_deterministic_hash`` and
    ``IdentifierRegistry._generate_deterministic_hash``. Kept local here to
    avoid coupling the API layer to the builder internals.

    Example:
        _deterministic_hash("Account", "REGION") → "A1B2"
    """
    seed = f"{entity_name}{field_name}".encode("utf-8")
    return hashlib.sha256(seed).hexdigest()[:4].upper()


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/bulk-resolve", response_model=BulkResolveResponse, summary="Auto-resolve all naming collisions")
def bulk_resolve_collisions(body: BulkResolveRequest) -> BulkResolveResponse:
    """Pre-compute deterministic hash-based target names for all colliding fields.

    This endpoint does **not** write to the database. It returns a suggestion
    list for frontend review. The user can then approve, edit, or reject each
    suggestion individually before committing the mapping.

    All suggested target names are:
    - UPPERCASE (Snowflake default identifier compliance)
    - Suffixed with a 4-char SHA-256 hash derived from entity + field name
    - Stable: re-calling this endpoint with the same inputs yields the same output
    """
    resolved: List[ResolvedField] = []

    for collision in body.collisions:
        entity = (collision.entity_name or "").strip()
        field = (collision.field_name or "").strip()
        base_target = (collision.current_target or field).strip().upper()

        hash_suffix = _deterministic_hash(entity, field)
        suggested = f"{base_target}_{hash_suffix}"

        resolved.append(
            ResolvedField(
                entity_name=entity,
                field_name=field,
                current_target=collision.current_target,
                suggested_target=suggested,
                hash_suffix=hash_suffix,
                status="suggestion",
            )
        )

    return BulkResolveResponse(resolved=resolved, total=len(resolved))
