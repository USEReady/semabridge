"""Regression tests for the shared business-field-type-hints module,
which replaced 3 duplicated copies of a "_business_rule_type"/
"business_rule_type" function (converter/tmsl_to_osi.py,
connectors/schema_manager.py, connectors/snowflake_emitter_parts/
schema_evolution.py).

Two bugs fixed:
1. An exact-match special case for one customer's literal column name
   ("FIRMNESSOFFIRSTDELIVERYDATE" -> force VARCHAR) — a demo-project
   fixture with zero generality, removed entirely.
2. Unbounded substring matches ("AMOUNT" in name, "DATE" in name)
   false-positive on unrelated names ("Paramount" contains "amount",
   "Validated" contains "date") — replaced with whole-word tokenization.

Synthetic placeholder names only.
"""
from semabridge.connectors.business_field_type_hints import infer_business_field_type
from semabridge.connectors.schema_manager import SnowflakeSchemaManager
from semabridge.connectors.snowflake_emitter_parts import schema_evolution
from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
from semabridge.intermediate.models import OSIDataType


def test_whole_word_matches_are_detected():
    assert infer_business_field_type("Accounts", "SystemModstamp") == "TIMESTAMP"
    assert infer_business_field_type("Orders", "TotalAmount") == "FLOAT"
    assert infer_business_field_type("Orders", "TradeVolume") == "FLOAT"
    assert infer_business_field_type("Orders", "CreatedDate") == "DATE"
    assert infer_business_field_type("Orders", "DELETED") == "BOOLEAN"


def test_mid_word_substring_is_not_a_false_positive():
    """The exact bug: these contain "amount"/"date" as substrings but are
    unrelated names."""
    assert infer_business_field_type("Companies", "Paramount") is None
    assert infer_business_field_type("Records", "IsValidated") is None


def test_field_history_tables_are_excluded_from_date_inference():
    assert infer_business_field_type("OpportunityFieldHistory", "CreatedDate") is None


def test_customer_specific_fixture_is_gone():
    """The removed fixture used to special-case this exact column name to
    force VARCHAR despite its name; it must now fall through to the same
    general whole-word "DATE" rule as any other date-like column name."""
    assert infer_business_field_type("AnyTable", "FirmnessOfFirstDeliveryDate") == "DATE"


def test_three_call_sites_delegate_to_the_shared_function():
    assert SnowflakeSchemaManager._business_rule_type("Orders", "TotalAmount") == "FLOAT"
    assert schema_evolution.business_rule_type("Orders", "TotalAmount") == "FLOAT"
    assert (
        TMSLToOSIConverter._business_rule_type("Orders", "TotalAmount")
        == OSIDataType.FLOAT
    )
    assert TMSLToOSIConverter._business_rule_type("Orders", "SystemModstamp") == OSIDataType.DATETIME
    assert TMSLToOSIConverter._business_rule_type("Orders", "Paramount") is None
