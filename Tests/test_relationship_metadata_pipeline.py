"""Tests for preserving relationship metadata end-to-end through TMSL, OSI, and SML.

Verifies:
1. One-to-One, Both, Active
2. One-to-One, Both, Inactive
3. Many-to-One, Single, Active
4. Missing or unknown metadata fallback handling
5. Verification through the entire conversion pipeline (TMSL -> OSI -> SML -> Normalization)
"""
from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
from semabridge.converter.tmsl_to_osi import (
    OSICardinality,
    OSICrossFilterDirection,
    TMSLToOSIConverter,
)
from semabridge.converter.osi_to_sml import (
    OSIToSMLConverter,
    SMLCardinality,
    SMLCrossFilterDirection,
)
from semabridge.core.engine.conversion import base as _conv_base


class DummyEngine:
    _has_path = _conv_base._has_path
    _select_strongest_relationship = _conv_base._select_strongest_relationship
    _normalize_relationships_for_target = _conv_base._normalize_relationships_for_target


def test_tmsl_fallback_normalizer_mappings():
    """Verify LocalPBIXConnector static normalizers for cardinality and cross-filter."""
    # 1. One-to-One, Both
    assert LocalPBIXConnector._normalize_pbixray_cardinality("1:1") == "OneToOne"
    assert LocalPBIXConnector._normalize_pbixray_cardinality("OneToOne") == "OneToOne"
    assert LocalPBIXConnector._normalize_pbixray_cross_filtering("Both") == "Both"
    assert LocalPBIXConnector._normalize_pbixray_cross_filtering("bothDirections") == "Both"

    # 2. Many-to-One, Single
    assert LocalPBIXConnector._normalize_pbixray_cardinality("M:1") == "ManyToOne"
    assert LocalPBIXConnector._normalize_pbixray_cardinality("many-to-one") == "ManyToOne"
    assert LocalPBIXConnector._normalize_pbixray_cross_filtering("Single") == "Single"
    assert LocalPBIXConnector._normalize_pbixray_cross_filtering("oneDirection") == "Single"

    # 3. Missing or unknown fallbacks
    assert LocalPBIXConnector._normalize_pbixray_cardinality(None) == "ManyToOne"
    assert LocalPBIXConnector._normalize_pbixray_cardinality("unknown_xyz") == "ManyToOne"
    assert LocalPBIXConnector._normalize_pbixray_cross_filtering(None) == "Single"
    assert LocalPBIXConnector._normalize_pbixray_cross_filtering("unknown_xyz") == "Single"


def test_relationship_metadata_pipeline_end_to_end():
    """Test relationship metadata flow through TMSL -> OSI -> SML -> Normalization."""
    fallback_result = {
        "models": [{"name": "TestModel"}],
        "tables": [
            {
                "name": "Customer",
                "columns": [{"name": "ID", "data_type": "int64"}],
            },
            {
                "name": "Sales",
                "columns": [{"name": "ID", "data_type": "int64"}],
            },
            {
                "name": "Product",
                "columns": [{"name": "ProductID", "data_type": "int64"}, {"name": "Product", "data_type": "string"}],
            },
            {
                "name": "Associated Product",
                "columns": [{"name": "ProductID", "data_type": "int64"}, {"name": "Product", "data_type": "string"}],
            },
        ],
        "measures": [],
        "relationships": [
            # 1. One-to-One, Both, Active
            {
                "name": "rel_active_1to1",
                "from_table": "Customer",
                "from_column": "ID",
                "to_table": "Sales",
                "to_column": "ID",
                "cardinality": "1:1",
                "cross_filtering_behavior": "Both",
                "is_active": True,
            },
            # 2. One-to-One, Both, Inactive
            {
                "name": "rel_inactive_1to1",
                "from_table": "Product",
                "from_column": "Product",
                "to_table": "Associated Product",
                "to_column": "Product",
                "cardinality": "1:1",
                "cross_filtering_behavior": "bothDirections",
                "is_active": False,
            },
            # 3. Many-to-One, Single, Active
            {
                "name": "rel_active_mto1",
                "from_table": "Sales",
                "from_column": "ProductID",
                "to_table": "Product",
                "to_column": "ProductID",
                "cardinality": "M:1",
                "cross_filtering_behavior": "oneDirection",
                "is_active": True,
            },
            # 4. Missing/unknown metadata fallback
            {
                "name": "rel_fallback",
                "from_table": "Customer",
                "from_column": "ProductID",
                "to_table": "Product",
                "to_column": "ProductID",
                "cardinality": None,
                "cross_filtering_behavior": None,
                "is_active": True,
            },
        ],
    }

    connector = LocalPBIXConnector.__new__(LocalPBIXConnector)
    connector._pbix_path = None
    tmsl_payload = connector._build_tmsl_from_fallback(fallback_result)
    tmsl_rels = tmsl_payload["model"]["relationships"]

    # Verify TMSL representation
    assert len(tmsl_rels) == 4
    assert tmsl_rels[0]["cardinality"] == "OneToOne" and tmsl_rels[0]["crossFilteringBehavior"] == "Both" and tmsl_rels[0]["isActive"] is True
    assert tmsl_rels[1]["cardinality"] == "OneToOne" and tmsl_rels[1]["crossFilteringBehavior"] == "Both" and tmsl_rels[1]["isActive"] is False
    assert tmsl_rels[2]["cardinality"] == "ManyToOne" and tmsl_rels[2]["crossFilteringBehavior"] == "Single" and tmsl_rels[2]["isActive"] is True
    assert tmsl_rels[3]["cardinality"] == "ManyToOne" and tmsl_rels[3]["crossFilteringBehavior"] == "Single" and tmsl_rels[3]["isActive"] is True

    # Verify OSI conversion
    osi_conv = TMSLToOSIConverter()
    osi_model = osi_conv.to_osi({"tmsl": tmsl_payload, "dataset_id": "TestModel"})
    assert len(osi_model.relationships) == 4
    assert osi_model.relationships[0].cardinality == OSICardinality.ONE_TO_ONE
    assert osi_model.relationships[0].cross_filter_direction == OSICrossFilterDirection.BOTH
    assert osi_model.relationships[0].is_active is True

    assert osi_model.relationships[1].cardinality == OSICardinality.ONE_TO_ONE
    assert osi_model.relationships[1].cross_filter_direction == OSICrossFilterDirection.BOTH
    assert osi_model.relationships[1].is_active is False

    assert osi_model.relationships[2].cardinality == OSICardinality.MANY_TO_ONE
    assert osi_model.relationships[2].cross_filter_direction == OSICrossFilterDirection.SINGLE

    assert osi_model.relationships[3].cardinality == OSICardinality.MANY_TO_ONE
    assert osi_model.relationships[3].cross_filter_direction == OSICrossFilterDirection.SINGLE

    # Verify SML conversion and normalization
    sml_conv = OSIToSMLConverter()
    sml_model = sml_conv.from_osi(osi_model)
    engine = DummyEngine()
    engine._normalize_relationships_for_target(sml_model)

    assert len(sml_model.relationships) == 4
    rel0 = next(r for r in sml_model.relationships if r.from_dataset == "Customer" and r.to_dataset == "Sales")
    assert rel0.cardinality == SMLCardinality.ONE_TO_ONE
    assert rel0.cross_filter == SMLCrossFilterDirection.BOTH
    assert rel0.is_active is True

    rel1 = next(r for r in sml_model.relationships if r.from_dataset == "Product" and r.to_dataset == "Associated Product")
    assert rel1.cardinality == SMLCardinality.ONE_TO_ONE
    assert rel1.cross_filter == SMLCrossFilterDirection.BOTH
    assert rel1.is_active is False
