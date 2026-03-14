"""
Adapter interfaces for the Semantic API.
"""

from semabridge.repository.interfaces.adapter_rollback_interface import (
    AdapterRollbackInterface,
    RollbackResult,
)

__all__ = [
    "AdapterRollbackInterface",
    "RollbackResult",
]
