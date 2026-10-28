"""
Unit tests for relationship extraction and normalizer across both paths.

Tests:
1. Cardinality normalizer for every format:
   - fromCardinality/toCardinality pairs ('many'/'one', numeric 1/2, etc.)
   - 'cardinality' key (ManyToOne, OneToMany, OneToOne, ManyToMany)
   - Shorthand (M:1, 1:M, 1:1, M:M)
   - Hyphenated / delimited (many-to-one, one-to-many, etc.)
   - pbixray numeric codes
2. Cross-filter normalizer for every format:
   - Single, OneDirection, Both, BothDirections, Automatic
   - pbixray numeric codes (1 = OneDirection, 2 = BothDirections, 3 = Automatic)
3. Active, joinOnDateBehavior, and relyOnReferentialIntegrity propagation.
4. Visibility of errors: unknown or invalid values must raise ConversionError (never default silently).
5. Documented defaults applied ONLY when source format genuinely has no field.
6. End-to-end extraction consistency between JSON path (.pbit) and fallback path (pbixray).
"""

import json
import os
import tempfile
import zipfile
from typing import Any, Dict

import pytest

from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.converter.relationship_normalizer import (
    normalize_cardinality,
    normalize_cross_filter,
    normalize_is_active,
    normalize_join_on_date_behavior,
    normalize_rely_on_referential_integrity,
)
from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
from semabridge.core.exceptions import ConversionError
from semabridge.intermediate.models import OSICardinality, OSICrossFilterDirection
from semabridge.sml.models import Cardinality as SMLCardinality, CrossFilterDirection as SMLCrossFilterDirection


# ---------------------------------------------------------------------------
# 1. Cardinality Normalizer Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "payload, expected",
    [
        # fromCardinality / toCardinality string pairs
        ({"fromCardinality": "many", "toCardinality": "one"}, OSICardinality.MANY_TO_ONE),
        ({"fromCardinality": "one", "toCardinality": "many"}, OSICardinality.ONE_TO_MANY),
        ({"fromCardinality": "one", "toCardinality": "one"}, OSICardinality.ONE_TO_ONE),
        ({"fromCardinality": "many", "toCardinality": "many"}, OSICardinality.MANY_TO_MANY),
        # Case insensitivity
        ({"fromCardinality": "Many", "toCardinality": "One"}, OSICardinality.MANY_TO_ONE),
        # Shorthand inside endpoint: 'm', '1'
        ({"fromCardinality": "m", "toCardinality": "1"}, OSICardinality.MANY_TO_ONE),
        ({"fromCardinality": "1", "toCardinality": "m"}, OSICardinality.ONE_TO_MANY),
        # pbixray numeric codes (1 = one, 2 = many)
        ({"fromCardinality": 2, "toCardinality": 1}, OSICardinality.MANY_TO_ONE),
        ({"fromCardinality": 1, "toCardinality": 2}, OSICardinality.ONE_TO_MANY),
        ({"fromCardinality": 1, "toCardinality": 1}, OSICardinality.ONE_TO_ONE),
        ({"fromCardinality": 2, "toCardinality": 2}, OSICardinality.MANY_TO_MANY),
        # Snake_case variant
        ({"from_cardinality": 2, "to_cardinality": 1}, OSICardinality.MANY_TO_ONE),
        # "cardinality" key: PascalCase
        ({"cardinality": "ManyToOne"}, OSICardinality.MANY_TO_ONE),
        ({"cardinality": "OneToMany"}, OSICardinality.ONE_TO_MANY),
        ({"cardinality": "OneToOne"}, OSICardinality.ONE_TO_ONE),
        ({"cardinality": "ManyToMany"}, OSICardinality.MANY_TO_MANY),
        # "cardinality" key: lower/mixed
        ({"cardinality": "manytoone"}, OSICardinality.MANY_TO_ONE),
        ({"cardinality": "onetomany"}, OSICardinality.ONE_TO_MANY),
        # Shorthand: M:1, 1:M, 1:1, M:M
        ({"cardinality": "M:1"}, OSICardinality.MANY_TO_ONE),
        ({"cardinality": "m:1"}, OSICardinality.MANY_TO_ONE),
        ({"cardinality": "1:M"}, OSICardinality.ONE_TO_MANY),
        ({"cardinality": "1:m"}, OSICardinality.ONE_TO_MANY),
        ({"cardinality": "1:1"}, OSICardinality.ONE_TO_ONE),
        ({"cardinality": "M:M"}, OSICardinality.MANY_TO_MANY),
        ({"cardinality": "m:m"}, OSICardinality.MANY_TO_MANY),
        # Hyphenated / delimited
        ({"cardinality": "many-to-one"}, OSICardinality.MANY_TO_ONE),
        ({"cardinality": "one-to-many"}, OSICardinality.ONE_TO_MANY),
        ({"cardinality": "one-to-one"}, OSICardinality.ONE_TO_ONE),
        ({"cardinality": "many-to-many"}, OSICardinality.MANY_TO_MANY),
        ({"cardinality": "many_to_one"}, OSICardinality.MANY_TO_ONE),
        ({"cardinality": "many to one"}, OSICardinality.MANY_TO_ONE),
    ],
)
def test_normalize_cardinality_valid(payload: Dict[str, Any], expected: OSICardinality):
    result = normalize_cardinality(payload, rel_name="test_rel")
    assert result == expected


def test_normalize_cardinality_genuine_absence():
    """Genuinely missing cardinality field defaults to MANY_TO_ONE and logs once."""
    payload: Dict[str, Any] = {"fromTable": "A", "toTable": "B"}
    result = normalize_cardinality(payload, rel_name="genuinely_missing_card")
    assert result == OSICardinality.MANY_TO_ONE


@pytest.mark.parametrize(
    "payload",
    [
        {"cardinality": "unknown_cardinality"},
        {"cardinality": ""},
        {"cardinality": "   "},
        {"fromCardinality": "many"},  # missing toCardinality
        {"toCardinality": "one"},     # missing fromCardinality
        {"fromCardinality": "invalid", "toCardinality": "one"},
        {"fromCardinality": 99, "toCardinality": 1},
        {"cardinality": 12345},
    ],
)
def test_normalize_cardinality_invalid_fails(payload: Dict[str, Any]):
    """Unknown or invalid cardinality must fail visibly (ConversionError)."""
    with pytest.raises(ConversionError):
        normalize_cardinality(payload, rel_name="invalid_rel")


# ---------------------------------------------------------------------------
# 2. Cross-Filter Normalizer Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "payload, expected",
    [
        # String representations
        ({"crossFilteringBehavior": "Single"}, OSICrossFilterDirection.SINGLE),
        ({"crossFilteringBehavior": "single"}, OSICrossFilterDirection.SINGLE),
        ({"crossFilteringBehavior": "OneDirection"}, OSICrossFilterDirection.SINGLE),
        ({"crossFilteringBehavior": "onedirection"}, OSICrossFilterDirection.SINGLE),
        ({"crossFilteringBehavior": "one_direction"}, OSICrossFilterDirection.SINGLE),
        ({"crossFilteringBehavior": "Both"}, OSICrossFilterDirection.BOTH),
        ({"crossFilteringBehavior": "both"}, OSICrossFilterDirection.BOTH),
        ({"crossFilteringBehavior": "BothDirections"}, OSICrossFilterDirection.BOTH),
        ({"crossFilteringBehavior": "bothdirections"}, OSICrossFilterDirection.BOTH),
        ({"crossFilteringBehavior": "both_directions"}, OSICrossFilterDirection.BOTH),
        ({"crossFilteringBehavior": "Automatic"}, OSICrossFilterDirection.AUTOMATIC),
        ({"crossFilteringBehavior": "automatic"}, OSICrossFilterDirection.AUTOMATIC),
        ({"crossFilteringBehavior": "auto"}, OSICrossFilterDirection.AUTOMATIC),
        # Numeric codes: 1 = OneDirection, 2 = BothDirections, 3 = Automatic
        ({"crossFilteringBehavior": 1}, OSICrossFilterDirection.SINGLE),
        ({"crossFilteringBehavior": 2}, OSICrossFilterDirection.BOTH),
        ({"crossFilteringBehavior": 3}, OSICrossFilterDirection.AUTOMATIC),
        # Numeric codes as strings
        ({"crossFilteringBehavior": "1"}, OSICrossFilterDirection.SINGLE),
        ({"crossFilteringBehavior": "2"}, OSICrossFilterDirection.BOTH),
        ({"crossFilteringBehavior": "3"}, OSICrossFilterDirection.AUTOMATIC),
        # Alternative key names
        ({"cross_filtering_behavior": "Both"}, OSICrossFilterDirection.BOTH),
        ({"crossFilterDirection": "bothDirections"}, OSICrossFilterDirection.BOTH),
        ({"cross_filter": "BothDirections"}, OSICrossFilterDirection.BOTH),
    ],
)
def test_normalize_cross_filter_valid(payload: Dict[str, Any], expected: OSICrossFilterDirection):
    result = normalize_cross_filter(payload, rel_name="test_rel")
    assert result == expected


def test_normalize_cross_filter_genuine_absence():
    """Genuinely missing cross-filter field defaults to SINGLE and logs once."""
    payload: Dict[str, Any] = {"fromTable": "A", "toTable": "B"}
    result = normalize_cross_filter(payload, rel_name="genuinely_missing_cf")
    assert result == OSICrossFilterDirection.SINGLE


@pytest.mark.parametrize(
    "payload",
    [
        {"crossFilteringBehavior": "bogus_direction"},
        {"crossFilteringBehavior": ""},
        {"crossFilteringBehavior": "   "},
        {"crossFilteringBehavior": 99},
        {"crossFilteringBehavior": 0},
        {"crossFilteringBehavior": 4},
        {"cross_filtering_behavior": "multi"},
    ],
)
def test_normalize_cross_filter_invalid_fails(payload: Dict[str, Any]):
    """Unknown or invalid cross-filter must fail visibly (ConversionError)."""
    with pytest.raises(ConversionError):
        normalize_cross_filter(payload, rel_name="invalid_rel")


# ---------------------------------------------------------------------------
# 3. Metadata Flags Tests
# ---------------------------------------------------------------------------

def test_metadata_flags_normalization():
    # isActive
    assert normalize_is_active({"isActive": True}) is True
    assert normalize_is_active({"isActive": False}) is False
    assert normalize_is_active({"isActive": 1}) is True
    assert normalize_is_active({"isActive": 0}) is False
    assert normalize_is_active({"is_active": "false"}) is False
    assert normalize_is_active({}) is True

    # joinOnDateBehavior
    assert normalize_join_on_date_behavior({"joinOnDateBehavior": 1}) == "dateAndTime"
    assert normalize_join_on_date_behavior({"joinOnDateBehavior": 2}) == "dateOnly"
    assert normalize_join_on_date_behavior({"joinOnDateBehavior": "dateAndTime"}) == "dateAndTime"
    assert normalize_join_on_date_behavior({"joinOnDateBehavior": "dateOnly"}) == "dateOnly"
    assert normalize_join_on_date_behavior({}) is None

    # relyOnReferentialIntegrity
    assert normalize_rely_on_referential_integrity({"relyOnReferentialIntegrity": 1}) is True
    assert normalize_rely_on_referential_integrity({"relyOnReferentialIntegrity": 0}) is False
    assert normalize_rely_on_referential_integrity({"relyOnReferentialIntegrity": True}) is True
    assert normalize_rely_on_referential_integrity({"relyOnReferentialIntegrity": False}) is False
    assert normalize_rely_on_referential_integrity({}) is None


# ---------------------------------------------------------------------------
# 4. End-to-End Consistency: .pbit (JSON path) vs Fallback (pbixray path)
# ---------------------------------------------------------------------------

def test_relationship_extraction_both_paths_identical():
    """Verify that both JSON path (.pbit) and pbixray fallback extract identical relationship models."""
    rel_data = {
        "name": "Orders_Customers",
        "fromTable": "Orders",
        "fromColumn": "CustomerID",
        "toTable": "Customers",
        "toColumn": "CustomerID",
        "fromCardinality": "many",
        "toCardinality": "one",
        "crossFilteringBehavior": "bothDirections",
        "isActive": False,
        "joinOnDateBehavior": "dateAndTime",
        "relyOnReferentialIntegrity": True,
    }

    # 1. JSON path via .pbit
    schema = {
        "name": "Model",
        "compatibilityLevel": 1550,
        "culture": "en-US",
        "tables": [
            {"name": "Orders", "columns": [{"name": "CustomerID", "dataType": "int64"}]},
            {"name": "Customers", "columns": [{"name": "CustomerID", "dataType": "int64"}]},
        ],
        "relationships": [rel_data],
    }

    tmpdir = tempfile.mkdtemp()
    pbit_path = os.path.join(tmpdir, "model.pbit")
    with zipfile.ZipFile(pbit_path, "w") as z:
        z.writestr("DataModelSchema", json.dumps(schema))

    connector = LocalPBIXConnector({"pbix_path": pbit_path})
    discovered_json = connector.discover()
    tmsl_json = discovered_json["raw_tmsl"]

    converter = TMSLToOSIConverter()
    osi_json = converter.to_osi({"tmsl": tmsl_json, "dataset_id": "test_model"})
    assert len(osi_json.relationships) == 1
    rel_json = osi_json.relationships[0]

    assert rel_json.cardinality == OSICardinality.MANY_TO_ONE
    assert rel_json.cross_filter_direction == OSICrossFilterDirection.BOTH
    assert rel_json.is_active is False
    assert rel_json.join_on_date_behavior == "dateAndTime"
    assert rel_json.rely_on_referential_integrity is True

    # 2. Fallback path (simulating pbixray extraction)
    fallback_result = {
        "tables": [
            {"name": "Orders", "columns": [{"name": "CustomerID", "data_type": "int64"}]},
            {"name": "Customers", "columns": [{"name": "CustomerID", "data_type": "int64"}]},
        ],
        "relationships": [
            {
                "name": "Orders_Customers",
                "from_table": "Orders",
                "from_column": "CustomerID",
                "to_table": "Customers",
                "to_column": "CustomerID",
                "cardinality": "M:1",
                "cross_filtering_behavior": 2,  # pbixray numeric code for BothDirections
                "is_active": 0,                # pbixray numeric code for inactive
                "join_on_date_behavior": 1,    # pbixray numeric code for dateAndTime
                "rely_on_referential_integrity": 1,
            }
        ],
        "measures": [],
        "models": [{"name": "Model", "compatibility_level": 1550, "culture": "en-US"}],
    }

    tmsl_fallback = connector._build_tmsl_from_fallback(fallback_result)
    osi_fallback = converter.to_osi({"tmsl": tmsl_fallback, "dataset_id": "test_model"})
    assert len(osi_fallback.relationships) == 1
    rel_fallback = osi_fallback.relationships[0]

    assert rel_fallback.cardinality == rel_json.cardinality
    assert rel_fallback.cross_filter_direction == rel_json.cross_filter_direction
    assert rel_fallback.is_active == rel_json.is_active
    assert rel_fallback.join_on_date_behavior == rel_json.join_on_date_behavior
    assert rel_fallback.rely_on_referential_integrity == rel_json.rely_on_referential_integrity

    # 3. Verify SML conversion for both
    sml_converter = OSIToSMLConverter()
    sml_json = sml_converter.from_osi(osi_json)
    sml_fallback = sml_converter.from_osi(osi_fallback)

    assert len(sml_json.relationships) == 1
    assert len(sml_fallback.relationships) == 1
    s_j = sml_json.relationships[0]
    s_f = sml_fallback.relationships[0]

    assert s_j.cardinality == SMLCardinality.MANY_TO_ONE
    assert s_j.cross_filter == SMLCrossFilterDirection.BOTH
    assert s_j.is_active is False
    assert s_j.join_on_date_behavior == "dateAndTime"
    assert s_j.rely_on_referential_integrity is True

    assert s_f.cardinality == s_j.cardinality
    assert s_f.cross_filter == s_j.cross_filter
    assert s_f.is_active == s_j.is_active
    assert s_f.join_on_date_behavior == s_j.join_on_date_behavior
    assert s_f.rely_on_referential_integrity == s_j.rely_on_referential_integrity
