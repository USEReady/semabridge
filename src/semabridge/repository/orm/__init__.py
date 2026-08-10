"""
ORM model package — imports all models so Alembic autogenerate can find them.

SQLAlchemy 2.0 ORM layer for SemaBridge.

Provides:
  - DeclarativeBase and all mapped models
  - setup_database() factory that reads DATABASE_URL
  - Singleton session factory via session_factory module
"""

from semabridge.repository.orm.base import Base
from semabridge.repository.orm.auth_models import (
    User,
    UserCredential,
    RefreshToken,
    PasswordResetToken,
)
from semabridge.repository.orm.account_models import (
    Account,
    Post,
)
from semabridge.repository.orm.project_models import (
    Project,
    LocalFolder,
    RetentionPolicy,
)
from semabridge.repository.orm.run_models import (
    Run,
    SourceArtifact,
    SyncConflictRow,
    SyncCheckpointRow,
)
from semabridge.repository.orm.snapshot_models import (
    SnapshotRow,
    Change,
    ModelVersion,
    ModelVersionHistory,
    SchemaVersionRow,
)
from semabridge.repository.orm.schedule_models import (
    SyncJob,
    SyncJobItem,
)
from semabridge.repository.orm.mapping_models import (
    ModelMappingRow,
    SynonymOverride,
    PrecomputeAggregationOverride,
)
from semabridge.repository.orm.infra_models import (
    Credential,
    CommandLog,
)

__all__ = [
    "Base",
    # Auth/identity
    "User",
    "UserCredential",
    "RefreshToken",
    "PasswordResetToken",
    # External accounts
    "Account",
    "Post",
    # Project lifecycle
    "Project",
    "LocalFolder",
    "RetentionPolicy",
    # Execution runs
    "Run",
    "SourceArtifact",
    "SyncConflictRow",
    "SyncCheckpointRow",
    # Version snapshots
    "SnapshotRow",
    "Change",
    "ModelVersion",
    "ModelVersionHistory",
    "SchemaVersionRow",
    # Job scheduling
    "SyncJob",
    "SyncJobItem",
    # Field mappings
    "ModelMappingRow",
    "SynonymOverride",
    "PrecomputeAggregationOverride",
    # Infrastructure
    "Credential",
    "CommandLog",
]
