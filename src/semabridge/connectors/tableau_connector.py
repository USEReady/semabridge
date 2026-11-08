"""Tableau Data Source (.tds / .twb) ingestion — NOT SUPPORTED YET.

A previous prototype here parsed Tableau XML into an OSI model, but it was
never wired into the conversion pipeline and its coverage was too thin to
ship: every column landed on the first relation regardless of its parent
table, joins/relationships were never read (so multi-table sources produced
models with no relationships), LOD/table calculations were passed through as
if they were SQL aggregates, and aggregation types were guessed from the
formula prefix. Producing a model from that would silently drop or alter
source semantics, so the connector now refuses explicitly instead.
"""

from __future__ import annotations

from typing import Optional

from semabridge.intermediate.models import OSIModel


class SourceNotSupportedError(NotImplementedError):
    """Raised when a BI source format is recognised but not supported yet."""

    def __init__(self, source: str, reason: str) -> None:
        self.source = source
        self.reason = reason
        super().__init__(f"{source} ingestion is not supported yet: {reason}")


TABLEAU_UNSUPPORTED_REASON = (
    "Tableau .tds/.twb parsing does not yet resolve column-to-table ownership, "
    "joins/relationships, or Tableau calculation semantics (LOD, table "
    "calculations), so a converted model would silently lose or alter source "
    "semantics. Export the underlying model to a supported source (Power BI "
    ".pbix, Fabric/TMSL, or Snowflake) instead."
)


class TableauConnector:
    """Placeholder for Tableau ingestion; every extraction raises."""

    def __init__(self, file_path: str):
        self.file_path = file_path

    def extract_osi_model(self, model_name: Optional[str] = None) -> OSIModel:
        raise SourceNotSupportedError("Tableau", TABLEAU_UNSUPPORTED_REASON)
