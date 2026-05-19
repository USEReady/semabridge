"""
SQLAlchemy 2.0 ORM layer for SemaBridge.

Provides:
  - DeclarativeBase and all mapped models
  - setup_database() factory that reads DATABASE_URL
  - Singleton session factory via session_factory module
"""

from semabridge.repository.orm.base import Base
from semabridge.repository.orm.models import (
    # Application layer
    ModelVersionHistory,
    Post,
    User,
    UserCredential,
    # Version-control layer
    Project,
    SnapshotRow,
    Change,
    Run,
    SourceArtifact,
    ModelVersion,
    # Sync engine layer
    SyncJob,
    SyncJobItem,
    ModelMappingRow,
    SchemaVersionRow,
    SyncConflictRow,
    SyncCheckpointRow,
    # Credential & audit layer
    Credential,
    CommandLog,
    SynonymOverride,
)

__all__ = [
    "Base",
    # Application layer
    "User",
    "UserCredential",
    "Post",
    "ModelVersionHistory",
    # Version-control layer
    "Project",
    "SnapshotRow",
    "Change",
    "Run",
    "SourceArtifact",
    "ModelVersion",
    # Sync engine layer
    "SyncJob",
    "SyncJobItem",
    "ModelMappingRow",
    "SchemaVersionRow",
    "SyncConflictRow",
    "SyncCheckpointRow",
    # Credential & audit layer
    "Credential",
    "CommandLog",
    "SynonymOverride",
]
