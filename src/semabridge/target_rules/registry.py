"""Which target platforms have rules. Each platform registers itself
(see snowflake/__init__.py); shared code looks rules up by platform name."""

from __future__ import annotations

from typing import Dict, Tuple

from semabridge.target_rules.base import TargetRules

_REGISTRY: Dict[str, TargetRules] = {}


class UnsupportedTargetError(ValueError):
    """No rules exist for the requested target platform yet."""


def _key(target: str) -> str:
    return str(target or "").strip().lower()


def register_target_rules(rules: TargetRules) -> TargetRules:
    """Make `rules` available under rules.target. Registering the same
    object again is a no-op; a different object for a taken name is refused."""
    key = _key(rules.target)
    if not key:
        raise ValueError("TargetRules.target must be a non-empty platform name")
    existing = _REGISTRY.get(key)
    if existing is not None and existing is not rules:
        raise ValueError(f"rules for {rules.target!r} are already registered")
    _REGISTRY[key] = rules
    return rules


def get_target_rules(target: str) -> TargetRules:
    """Rules for a target platform name such as "snowflake" (case-insensitive)."""
    try:
        return _REGISTRY[_key(target)]
    except KeyError:
        supported = ", ".join(sorted(_REGISTRY)) or "none"
        raise UnsupportedTargetError(
            f"No target rules for {target!r} yet (supported: {supported})."
        ) from None


def registered_targets() -> Tuple[str, ...]:
    return tuple(sorted(_REGISTRY))
