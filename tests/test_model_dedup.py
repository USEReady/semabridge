"""
Tests for Module 3: Deterministic Semantic Deduplication Pipeline.

Verifies canonical name normalization, structural fingerprinting,
and the full deduplication pipeline.
"""

import pytest
from unittest.mock import MagicMock

from semabridge.utils.model_dedup import (
    canonicalize_name,
    fingerprint_model,
    deduplicate_models,
    deduplicate_model_names,
    DuplicateGroup,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_col(name):
    col = MagicMock()
    col.unique_name = name
    return col


def _make_dataset(name, columns=None):
    ds = MagicMock()
    ds.unique_name = name
    ds.columns = columns or []
    return ds


def _make_metric(name, dataset="ds1", aggregation="sum"):
    m = MagicMock()
    m.unique_name = name
    m.dataset = dataset
    m.aggregation = aggregation
    return m


def _make_relationship(name, from_ds, to_ds):
    r = MagicMock()
    r.unique_name = name
    r.from_dataset = from_ds
    r.to_dataset = to_ds
    return r


def _make_model(name, datasets=None, metrics=None, relationships=None):
    model = MagicMock()
    model.unique_name = name
    model.label = name
    model.datasets = datasets or []
    model.metrics = metrics or []
    model.relationships = relationships or []
    return model


# ---------------------------------------------------------------------------
# canonicalize_name()
# ---------------------------------------------------------------------------

class TestCanonicalizeName:
    def test_spaces_to_underscores(self):
        assert canonicalize_name("Regional Sales Sample") == "regional_sales_sample"

    def test_underscores_preserved(self):
        assert canonicalize_name("Regional_Sales_Sample") == "regional_sales_sample"

    def test_hyphens_to_underscores(self):
        assert canonicalize_name("regional-sales-sample") == "regional_sales_sample"

    def test_mixed_case(self):
        assert canonicalize_name("MyModel") == "mymodel"

    def test_special_chars(self):
        assert canonicalize_name("  My Model! (v2)  ") == "my_model_v2"

    def test_empty_string(self):
        assert canonicalize_name("") == ""

    def test_numbers(self):
        assert canonicalize_name("model_123") == "model_123"

    def test_duplicate_pair(self):
        """The core use case: these two names must canonicalize the same."""
        a = canonicalize_name("Regional_Sales_Sample")
        b = canonicalize_name("Regional Sales Sample")
        assert a == b


# ---------------------------------------------------------------------------
# fingerprint_model()
# ---------------------------------------------------------------------------

class TestFingerprintModel:
    def test_same_structure_same_hash(self):
        """Two models with identical datasets/columns → same fingerprint."""
        m1 = _make_model("Model A", [
            _make_dataset("ds1", [_make_col("col_a"), _make_col("col_b")]),
        ])
        m2 = _make_model("Model B", [
            _make_dataset("ds1", [_make_col("col_a"), _make_col("col_b")]),
        ])
        assert fingerprint_model(m1) == fingerprint_model(m2)

    def test_different_structure_different_hash(self):
        """Models with different columns → different fingerprint."""
        m1 = _make_model("M1", [
            _make_dataset("ds1", [_make_col("col_a")]),
        ])
        m2 = _make_model("M2", [
            _make_dataset("ds1", [_make_col("col_x")]),
        ])
        assert fingerprint_model(m1) != fingerprint_model(m2)

    def test_different_datasets_different_hash(self):
        m1 = _make_model("M1", [_make_dataset("ds1")])
        m2 = _make_model("M2", [_make_dataset("ds2")])
        assert fingerprint_model(m1) != fingerprint_model(m2)

    def test_order_independence(self):
        """Column/dataset order should not affect fingerprint."""
        m1 = _make_model("M1", [
            _make_dataset("ds_b", [_make_col("c2"), _make_col("c1")]),
            _make_dataset("ds_a", [_make_col("c3")]),
        ])
        m2 = _make_model("M2", [
            _make_dataset("ds_a", [_make_col("c3")]),
            _make_dataset("ds_b", [_make_col("c1"), _make_col("c2")]),
        ])
        assert fingerprint_model(m1) == fingerprint_model(m2)

    def test_with_metrics(self):
        m1 = _make_model("M1", metrics=[_make_metric("revenue")])
        m2 = _make_model("M2", metrics=[_make_metric("revenue")])
        assert fingerprint_model(m1) == fingerprint_model(m2)

    def test_with_relationships(self):
        m1 = _make_model("M1", relationships=[
            _make_relationship("rel1", "ds1", "ds2"),
        ])
        m2 = _make_model("M2", relationships=[
            _make_relationship("rel1", "ds1", "ds2"),
        ])
        assert fingerprint_model(m1) == fingerprint_model(m2)


# ---------------------------------------------------------------------------
# deduplicate_models()
# ---------------------------------------------------------------------------

class TestDeduplicateModels:
    def test_no_duplicates(self):
        models = {
            "id1": _make_model("Alpha", [_make_dataset("ds_alpha", [_make_col("x")])]),
            "id2": _make_model("Beta", [_make_dataset("ds_beta", [_make_col("y")])]),
        }
        clean, dupes = deduplicate_models(models)
        assert len(clean) == 2
        assert len(dupes) == 0

    def test_name_duplicates_detected(self):
        """Models with canonically-identical names → one kept, one dropped."""
        models = {
            "id1": _make_model("Regional_Sales_Sample"),
            "id2": _make_model("Regional Sales Sample"),
        }
        clean, dupes = deduplicate_models(models)
        assert len(clean) == 1
        assert len(dupes) == 1
        assert dupes[0].reason == "name"
        assert dupes[0].kept == "id1"
        assert "id2" in dupes[0].dropped

    def test_structural_duplicates_detected(self):
        """Models with different names but identical structure → detected."""
        cols = [_make_col("A"), _make_col("B")]
        models = {
            "id1": _make_model("First", [_make_dataset("ds1", cols)]),
            "id2": _make_model("Second", [_make_dataset("ds1", cols)]),
        }
        clean, dupes = deduplicate_models(models)
        assert len(clean) == 1
        assert any(d.reason == "structure" for d in dupes)

    def test_strict_mode_raises(self):
        models = {
            "id1": _make_model("Regional_Sales_Sample"),
            "id2": _make_model("Regional Sales Sample"),
        }
        with pytest.raises(ValueError, match="strict mode"):
            deduplicate_models(models, strict=True)

    def test_empty_input(self):
        clean, dupes = deduplicate_models({})
        assert clean == {}
        assert dupes == []

    def test_single_model(self):
        models = {"id1": _make_model("Solo")}
        clean, dupes = deduplicate_models(models)
        assert len(clean) == 1
        assert dupes == []


# ---------------------------------------------------------------------------
# deduplicate_model_names()
# ---------------------------------------------------------------------------

class TestDeduplicateModelNames:
    def test_no_duplicates(self):
        names = ["Alpha", "Beta", "Gamma"]
        unique, dupes = deduplicate_model_names(names)
        assert unique == names
        assert dupes == []

    def test_detects_name_duplicates(self):
        names = [
            "Regional_Sales_Sample",
            "Regional Sales Sample",
            "Other Model",
        ]
        unique, dupes = deduplicate_model_names(names)
        assert len(unique) == 2
        assert "Regional_Sales_Sample" in unique
        assert "Other Model" in unique
        assert len(dupes) == 1
        assert dupes[0].canonical_name == "regional_sales_sample"

    def test_triple_duplicate(self):
        names = ["Model A", "model_a", "MODEL-A"]
        unique, dupes = deduplicate_model_names(names)
        assert len(unique) == 1
        assert unique[0] == "Model A"  # first wins
        assert len(dupes) == 1
        assert len(dupes[0].dropped) == 2

    def test_empty_list(self):
        unique, dupes = deduplicate_model_names([])
        assert unique == []
        assert dupes == []
