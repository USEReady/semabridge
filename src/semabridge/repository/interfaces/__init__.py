"""
Adapter interfaces for the Semantic API.
"""

from semabridge.repository.interfaces.adapter_rollback_interface import (
    AdapterRollbackInterface,
    RollbackResult,
)

from semabridge.repository.interfaces.repository_protocols import (
    IUserRepository,
    IAccountRepository,
    IProjectRepository,
    IRunRepository,
    ISnapshotRepository,
    IMappingRepository,
    ICredentialRepository,
    IModelVersionRepository,
)

__all__ = [
    "AdapterRollbackInterface",
    "RollbackResult",
    "IUserRepository",
    "IAccountRepository",
    "IProjectRepository",
    "IRunRepository",
    "ISnapshotRepository",
    "IMappingRepository",
    "ICredentialRepository",
    "IModelVersionRepository",
]
