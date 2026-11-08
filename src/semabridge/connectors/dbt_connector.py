"""dbt Semantic Layer (MetricFlow) ingestion — NOT SUPPORTED YET.

A previous prototype here parsed semantic_manifest.json into an OSI model,
but it was never wired into the conversion pipeline and its coverage was too
thin to ship: entities were never turned into relationships (every model had
zero joins), all non-time dimensions were typed STRING, ratio/derived/
cumulative/conversion metrics were rewritten as SUM(<metric name>), metric
filters were ignored, and a metric whose measure could not be resolved was
silently attached to the first semantic model. The connector now refuses
explicitly instead of returning a model with invented or missing semantics.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from semabridge.connectors.tableau_connector import SourceNotSupportedError
from semabridge.intermediate.models import OSIModel

DBT_UNSUPPORTED_REASON = (
    "dbt semantic manifests are not yet converted with entity-based "
    "relationships, typed dimensions, or ratio/derived/cumulative/conversion "
    "metric semantics, so a converted model would silently lose or alter "
    "source semantics."
)


class DbtSemanticConnector:
    """Placeholder for dbt Semantic Layer ingestion; every extraction raises."""

    def __init__(self, manifest_data: Dict[str, Any] | str):
        self.manifest_data = manifest_data

    def extract_osi_model(self, model_name: Optional[str] = None) -> OSIModel:
        raise SourceNotSupportedError("dbt Semantic Layer", DBT_UNSUPPORTED_REASON)
