"""The port every deployment target implements.

The engine's deploy step asks the registry for the run's target and calls
``deploy``; it no longer branches on target names, and a target nobody
registered is an error (it used to fall through and report success without
deploying anything). Adding a target = one adapter + one registration.

Built-in adapters wrap the existing per-target deploy functions
(core/engine/deployment/{fabric,snowflake,databricks}.py), so behaviour is
unchanged; new targets implement the protocol directly.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Protocol, runtime_checkable


class UnknownTargetError(ValueError):
    """The run's target_type has no registered adapter."""


@runtime_checkable
class TargetAdapter(Protocol):
    name: str

    def deploy(self, engine: Any, context: Any) -> None:
        """Publish ``context.sml_model`` to the target (Step 9). Raises on failure."""


@dataclass(frozen=True)
class EngineMethodAdapter:
    """Adapter that delegates to an ExecutionEngine deploy method."""

    name: str
    method_name: str

    def deploy(self, engine: Any, context: Any) -> None:
        getattr(engine, self.method_name)(context)


_registry: Dict[str, TargetAdapter] = {}
_lock = threading.Lock()


def register_target_adapter(adapter: TargetAdapter) -> None:
    with _lock:
        _registry[str(adapter.name).strip().lower()] = adapter


def registered_targets() -> List[str]:
    with _lock:
        return sorted(_registry)


def get_target_adapter(target_type: Any) -> TargetAdapter:
    key = str(target_type or "").strip().lower()
    with _lock:
        adapter = _registry.get(key)
    if adapter is None:
        raise UnknownTargetError(
            f"No deployment target named {target_type!r} (available: {', '.join(registered_targets()) or 'none'})."
        )
    return adapter


for _name, _method in (
    ("snowflake", "_deploy_to_snowflake"),
    ("databricks", "_deploy_to_databricks"),
    ("fabric", "_deploy_to_fabric"),
):
    register_target_adapter(EngineMethodAdapter(_name, _method))
