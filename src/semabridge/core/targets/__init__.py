"""Target platforms behind one interface (see adapter.py)."""
from semabridge.core.targets.adapter import (
    TargetAdapter,
    UnknownTargetError,
    get_target_adapter,
    register_target_adapter,
    registered_targets,
)

__all__ = [
    "TargetAdapter",
    "UnknownTargetError",
    "get_target_adapter",
    "register_target_adapter",
    "registered_targets",
]
