"""
Ray Task Coordinator for Semabridge.

Provides high-level async helpers that Temporal activities call to
submit diff work to the Ray cluster.  Falls back gracefully when Ray
is not initialised (e.g. local dev without a cluster).

Usage (from an activity):
    from semabridge.distributed.ray.coordinator import submit_diff
    result = await submit_diff(current_dict, previous_dict)
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

try:
    import ray
except ImportError:
    ray = None  # type: ignore[assignment]

from semabridge.distributed.ray.actor_diff import DiffActor, compute_sml_diff


# ---------------------------------------------------------------------------
# Cluster bootstrap
# ---------------------------------------------------------------------------


def ensure_ray_initialized(address: str = "auto") -> None:
    """Idempotently connect to an existing Ray cluster (or start a local one)."""
    if ray is None:
        raise ImportError("ray is not installed")
    if not ray.is_initialized():
        ray.init(address=address, ignore_reinit_error=True)


# ---------------------------------------------------------------------------
# Actor pool management
# ---------------------------------------------------------------------------

# Module-level cache of actor handles keyed by (tenant, model)
_actor_cache: Dict[str, Any] = {}  # values are ray.actor.ActorHandle


def _get_or_create_actor(
    tenant_id: str = "default",
    model_id: str = "default",
) -> Any:  # ray.actor.ActorHandle
    """Return an existing actor handle or create a new one."""
    key = f"{tenant_id}::{model_id}"
    if key not in _actor_cache:
        _actor_cache[key] = DiffActor.remote(tenant_id, model_id)
    return _actor_cache[key]


def clear_actor_cache() -> None:
    """Release all cached actor handles (useful in tests)."""
    _actor_cache.clear()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def submit_diff(
    current: Dict[str, Any],
    previous: Dict[str, Any],
    tenant_id: str = "default",
    model_id: str = "default",
    *,
    timeout_s: float = 60.0,
) -> Dict[str, Any]:
    """
    Submit a diff job to a Ray actor and await the result.

    If Ray is unavailable, falls back to computing the diff locally.

    Args:
        current:    The current SML dict extracted from Snowflake.
        previous:   The previous SML baseline dict.
        tenant_id:  Tenant identifier for actor affinity.
        model_id:   Model identifier for actor affinity.
        timeout_s:  Max seconds to wait for the Ray result.

    Returns:
        A dict with keys: added, modified, removed, changes, duration_ms.
    """
    try:
        ensure_ray_initialized()
        actor = _get_or_create_actor(tenant_id, model_id)
        ref = actor.diff.remote(current, previous)
        # ray.get is blocking – run in executor to keep async context free
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: ray.get(ref, timeout=timeout_s)
        )
        return result
    except Exception:
        # Fallback: local diff
        from semabridge.distributed.ray.actor_diff import compute_sml_diff

        diff_result = compute_sml_diff(previous, current)
        return diff_result.to_dict()


async def submit_batch_diff(
    pairs: list[tuple[Dict[str, Any], Dict[str, Any], str, str]],
    timeout_s: float = 120.0,
) -> list[Dict[str, Any]]:
    """
    Submit multiple diff jobs in parallel to separate Ray actors.

    Args:
        pairs: list of (current, previous, tenant_id, model_id) tuples.
        timeout_s: Max seconds for the entire batch.

    Returns:
        List of diff result dicts in the same order as *pairs*.
    """
    try:
        ensure_ray_initialized()
        refs = []
        for current, previous, tid, mid in pairs:
            actor = _get_or_create_actor(tid, mid)
            refs.append(actor.diff.remote(current, previous))

        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            None, lambda: ray.get(refs, timeout=timeout_s)
        )
        return results
    except Exception:
        # Fallback: sequential local diffs
        results = []
        for current, previous, _tid, _mid in pairs:
            r = compute_sml_diff(previous, current)
            results.append(r.to_dict())
        return results
