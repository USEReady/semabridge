"""Which target platforms have a backend; looked up by platform name."""

from __future__ import annotations

from typing import Callable, Dict, Tuple

from semabridge.target_backends.base import TargetBackend
from semabridge.target_rules import get_target_rules

_FACTORIES: Dict[str, Callable[[], TargetBackend]] = {}


class UnsupportedBackendError(ValueError):
    """No backend exists for the requested target platform."""


def _key(target: str) -> str:
    return str(target or "").strip().lower()


def register_backend(target: str, factory: Callable[[], TargetBackend]) -> None:
    key = _key(target)
    if not key:
        raise ValueError("target must be a non-empty platform name")
    existing = _FACTORIES.get(key)
    if existing is not None and existing is not factory:
        raise ValueError(f"a backend for {target!r} is already registered")
    _FACTORIES[key] = factory


def get_backend(target: str) -> TargetBackend:
    """A new backend for a platform name such as "databricks" (case-insensitive)."""
    try:
        factory = _FACTORIES[_key(target)]
    except KeyError:
        supported = ", ".join(sorted(_FACTORIES)) or "none"
        raise UnsupportedBackendError(
            f"No target backend for {target!r} (supported: {supported})."
        ) from None
    return factory()


def registered_backends() -> Tuple[str, ...]:
    return tuple(sorted(_FACTORIES))


def _databricks() -> TargetBackend:
    from semabridge.target_backends.databricks import DatabricksBackend

    return DatabricksBackend(get_target_rules("databricks"))


register_backend("databricks", _databricks)
