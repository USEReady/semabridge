# Backward-compatibility shim - do not add logic here.
# This file exists to preserve the import path used by external files and
# the dynamic string reference in websocket_alerts.py.
#
# All implementation has been moved to semabridge.core.engine package.
# This file is a re-export layer for backward compatibility.

from semabridge.core.engine import (  # noqa: F401
    ExecutionEngine,
    RunContext,
    ConfigValidationError,
    AuthenticationError,
    ExtractionError,
    SourceFormatError,
    ConversionError,
    PersistenceError,
    DeploymentError,
)
from semabridge.core.source_format import (  # noqa: F401
    SourceFormat,
    from_fabric_tmsl,
    from_pbix_tmsl,
    from_snowflake_metadata,
)
from semabridge.intermediate.models import OSIModel  # noqa: F401
from semabridge.sml.models import SMLModel, SMLRelationship  # noqa: F401
from semabridge.repository.model_repository import ModelRepository  # noqa: F401
from semabridge.core.sync_modes import apply_sync_mode  # noqa: F401
from semabridge.utils.logger import get_logger  # noqa: F401
from semabridge.utils.relationship_naming import generate_relationship_name  # noqa: F401
from semabridge.utils.identifiers import IdentifierSanitizer  # noqa: F401

