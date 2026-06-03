"""
Universal conversion router for SemaBridge.
Maps (source_type, target_type) → ordered list of pipeline steps.
"""
from __future__ import annotations
from typing import Any


class ConversionRouter:
    """Route any source→target conversion through the correct pipeline.

    Pipeline steps are symbolic — each step name corresponds to a converter
    or connector stage in core/engine.py.
    """

    PATHS: dict[tuple[str, str], list[str]] = {
        # Fabric (TMSL) → Snowflake semantic view
        ("fabric", "snowflake"): [
            "fabric_extract",    # FabricExtractor → TMSL model.bim
            "tmsl_to_osi",       # TMSLToOSIConverter
            "osi_to_sml",        # OSIToSMLConverter (includes DAX→SQL translation)
            "sml_to_snowflake",  # SnowflakeEmitter
        ],
        # Snowflake → Fabric (TMSL)
        ("snowflake", "fabric"): [
            "snowflake_extract", # SnowflakeExtractor + SemanticViewToOSIConverter
            "osi_to_sml",        # OSIToSMLConverter (includes SQL→DAX translation)
            "sml_to_tmsl",       # TMSLGenerator
            "tmsl_to_fabric",    # FabricPublisher
        ],
        # .pbix → Snowflake
        ("pbix", "snowflake"): [
            "pbix_extract",      # LocalPBIXConnector → TMSL
            "tmsl_to_osi",       # TMSLToOSIConverter (reuses Fabric parser)
            "osi_to_sml",        # OSIToSMLConverter
            "sml_to_snowflake",  # SnowflakeEmitter
        ],
        # .pbix → Fabric (publish to Fabric workspace)
        ("pbix", "fabric"): [
            "pbix_extract",      # LocalPBIXConnector → TMSL
            "tmsl_to_osi",       # TMSLToOSIConverter
            "osi_to_sml",        # OSIToSMLConverter
            "sml_to_tmsl",       # TMSLGenerator
            "tmsl_to_fabric",    # FabricPublisher
        ],
        # Snowflake → .pbix (via Fabric proxy — user downloads .pbix from Fabric UI)
        ("snowflake", "pbix"): [
            "snowflake_extract",
            "osi_to_sml",
            "sml_to_tmsl",
            "tmsl_to_fabric",    # Publishes to staging workspace; user downloads .pbix from there
        ],
        # Fabric → .pbix (re-publish to different workspace)
        ("fabric", "pbix"): [
            "fabric_extract",
            "tmsl_to_osi",
            "osi_to_sml",
            "sml_to_tmsl",
            "tmsl_to_fabric",
        ],
        # Fabric ↔ Fabric (re-publish to different workspace)
        ("fabric", "fabric"): [
            "fabric_extract",
            "tmsl_to_osi",
            "osi_to_sml",
            "sml_to_tmsl",
            "tmsl_to_fabric",
        ],
        # Snowflake ↔ Snowflake (re-deploy semantic view to different schema/db)
        ("snowflake", "snowflake"): [
            "snowflake_extract",
            "osi_to_sml",
            "sml_to_snowflake",
        ],
    }

    def route(self, source_type: str, target_type: str) -> list[str]:
        """Return ordered pipeline steps for (source_type, target_type).

        Raises ConversionError if no path exists.
        """
        key = (source_type.lower().strip(), target_type.lower().strip())
        if key not in self.PATHS:
            raise ValueError(
                f"No conversion path registered for {source_type!r} → {target_type!r}. "
                f"Supported paths: {[f'{s}→{t}' for s, t in self.PATHS]}"
            )
        return list(self.PATHS[key])

    def supported_paths(self) -> list[tuple[str, str]]:
        """Return all registered (source, target) pairs."""
        return list(self.PATHS.keys())

    def is_supported(self, source_type: str, target_type: str) -> bool:
        key = (source_type.lower().strip(), target_type.lower().strip())
        return key in self.PATHS


# Module-level singleton
_router: ConversionRouter | None = None


def get_router() -> ConversionRouter:
    global _router
    if _router is None:
        _router = ConversionRouter()
    return _router
