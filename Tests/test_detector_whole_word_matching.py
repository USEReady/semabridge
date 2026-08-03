"""Regression tests for the substring-vs-whole-word bug cluster across
connectors/inference_engine.py, connectors/measure_detector.py, and
connectors/relationship_detector.py.

All three files used unanchored `in` substring checks (or, for
relationship_detector.py, unanchored suffix checks and an unverified
name-pattern PK guess) to classify tables/columns as fact/dimension/date/
measure/foreign-key. Synthetic placeholder names only.
"""
from semabridge.connectors.inference_engine import SmlInferenceEngine
from semabridge.connectors.measure_detector import MeasureDetector
from semabridge.connectors.relationship_detector import RelationshipDetector
from semabridge.sml.models import AggregationType


# --- inference_engine.py ----------------------------------------------------

def test_table_name_date_override_is_whole_word_not_substring():
    """'Validated_Orders' contains 'DATE' as a substring but is not a date
    dimension; a genuine 'Calendar' table must still be caught by name even
    when its date-column ratio alone wouldn't trigger the override (kept
    low here deliberately so the name check is what's actually decisive)."""
    ten_cols_one_date = [{"name": "SomeDateCol", "data_type": "DATE"}] + [
        {"name": f"col{i}", "data_type": "VARCHAR"} for i in range(9)
    ]
    tables = {"Validated_Orders": {}, "Calendar": {}}
    columns = {"Validated_Orders": ten_cols_one_date, "Calendar": ten_cols_one_date}
    engine = SmlInferenceEngine(tables, columns, relationships=[], primary_keys={})
    scores = engine.classify()
    assert scores["Validated_Orders"].classification != "TIME"
    assert scores["Calendar"].classification == "TIME"


def test_column_level_date_detection_is_whole_word_not_substring():
    """'Update_Timestamp' contains 'DATE' as a substring (upDATEd) but is
    not itself a date-typed/date-named column signal beyond its real type."""
    tables = {"Widgets": {}}
    columns = {
        "Widgets": [
            {"name": "Update_Timestamp", "data_type": "VARCHAR"},
            {"name": "Order_Date", "data_type": "VARCHAR"},
        ]
    }
    engine = SmlInferenceEngine(tables, columns, relationships=[], primary_keys={})
    engine._calculate_base_metrics()
    # Only "Order_Date" (whole-word DATE) should count, not "Update_Timestamp".
    assert engine.scores["Widgets"].date_columns == 1


def test_measure_candidate_key_suffix_is_anchored_with_underscore():
    """A numeric column literally named 'Turkey' or 'Hockey' must not be
    excluded from measure candidates just for ending in the letters
    'key' — only a real _KEY-suffixed or bare KEY column should be."""
    tables = {"Sales": {}}
    columns = {
        "Sales": [
            {"name": "Turkey", "data_type": "INTEGER"},
            {"name": "Hockey", "data_type": "INTEGER"},
            {"name": "Product_Key", "data_type": "INTEGER"},
        ]
    }
    engine = SmlInferenceEngine(tables, columns, relationships=[], primary_keys={})
    engine._calculate_base_metrics()
    assert engine.scores["Sales"].measure_candidates == 2  # Turkey, Hockey — not Product_Key


# --- measure_detector.py ----------------------------------------------------

def test_exclude_patterns_do_not_false_positive_on_mid_word_substrings():
    """The exact bug: these numeric columns contain excluded-pattern
    substrings mid-word but are legitimate measures."""
    detector = MeasureDetector(tables={"T": {}}, columns={"T": []}, relationships=[])
    columns = [
        {"name": "Holiday_Bonus_Amount", "data_type": "FLOAT"},
        {"name": "Prior_Year_Revenue", "data_type": "FLOAT"},
        {"name": "Escrow_Amount", "data_type": "FLOAT"},
    ]
    measures = detector.detect_measures("T", columns, is_fact=True)
    detected_cols = {m["column"] for m in measures}
    assert detected_cols == {"Holiday_Bonus_Amount", "Prior_Year_Revenue", "Escrow_Amount"}


def test_exclude_patterns_still_exclude_genuine_whole_word_matches():
    detector = MeasureDetector(tables={"T": {}}, columns={"T": []}, relationships=[])
    columns = [
        {"name": "Order_Date", "data_type": "FLOAT"},
        {"name": "Batch_Number", "data_type": "FLOAT"},  # contains whole word "BATCH"
    ]
    measures = detector.detect_measures("T", columns, is_fact=False)
    detected_cols = {m["column"] for m in measures}
    assert detected_cols == set()


def test_measure_patterns_do_not_false_positive_on_mid_word_substring():
    """'Separate_Account' contains 'RATE' mid-word (sepa-RATE) but is not
    a rate measure — must not get AVG aggregation from that false match."""
    detector = MeasureDetector(tables={"T": {}}, columns={"T": []}, relationships=[])
    columns = [{"name": "Separate_Account_Balance", "data_type": "FLOAT"}]
    measures = detector.detect_measures("T", columns, is_fact=False)
    assert len(measures) == 1
    # Matched via whole-word "BALANCE", not the false "RATE" substring —
    # SUM aggregation, not AVG.
    assert measures[0]["aggregation"] == AggregationType.SUM.value


# --- relationship_detector.py -----------------------------------------------

def test_bare_id_key_suffixes_no_longer_false_positive():
    """'Paid'/'Valid'/'Guid' end in the letters 'id' but must not be
    treated as foreign-key columns."""
    tables = {"Orders": {}}
    columns = {"Orders": [{"name": "Paid"}, {"name": "Valid"}, {"name": "Guid"}]}
    detector = RelationshipDetector(tables, columns, primary_keys={})
    assert detector.detect_all() == []


def test_anchored_id_suffix_still_detected():
    tables = {"Widgets": {}, "Gadgets": {}}
    columns = {
        "Widgets": [{"name": "ID"}],
        "Gadgets": [{"name": "ID"}, {"name": "widget_id"}],
    }
    detector = RelationshipDetector(tables, columns, primary_keys={})
    rels = detector.detect_all()
    assert len(rels) == 1
    assert rels[0]["to_table"] == "Widgets"
    assert rels[0]["to_column"] == "ID"


def test_guessed_pk_gets_lower_confidence_than_a_real_declared_pk():
    tables = {"Widgets": {}, "Gadgets": {}}
    columns = {
        "Widgets": [{"name": "WidgetKey"}],
        "Gadgets": [{"name": "ID"}, {"name": "widget_id"}],
    }
    # No explicit primary_keys metadata for Widgets -> PK is a name-pattern guess.
    guessed = RelationshipDetector(tables, columns, primary_keys={}).detect_all()
    assert guessed == []  # "WidgetKey" doesn't match any of the guessed PK patterns either

    # Real column named exactly matching a guessed pattern, but with vs.
    # without explicit PK metadata, must report different confidence.
    columns2 = {
        "Widgets": [{"name": "ID"}],
        "Gadgets": [{"name": "ID"}, {"name": "widget_id"}],
    }
    without_pk_metadata = RelationshipDetector(tables, columns2, primary_keys={}).detect_all()
    with_pk_metadata = RelationshipDetector(
        tables, columns2, primary_keys={"Widgets": ["ID"]}
    ).detect_all()

    assert without_pk_metadata[0]["confidence"] == 0.5
    assert with_pk_metadata[0]["confidence"] == 0.8
