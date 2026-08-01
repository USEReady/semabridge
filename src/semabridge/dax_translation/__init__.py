"""DaxTranslationService — the unified DAX-to-SQL translation service.

New, additive package. Nothing here is wired into any existing pipeline yet
(Step 1 of the approved consolidation migration). Existing call sites
(connectors/translator.py, converter/dax_translator.py, databricks_publisher.py,
measure_sync.py, etc.) are untouched and continue to run exactly as before.

Public entry point: DaxTranslationService (see service.py).
"""

from semabridge.dax_translation.service import DaxTranslationService
from semabridge.dax_translation.types import (
    Dialect,
    TranslationRequest,
    TranslationResult,
)

__all__ = [
    "DaxTranslationService",
    "Dialect",
    "TranslationRequest",
    "TranslationResult",
]
