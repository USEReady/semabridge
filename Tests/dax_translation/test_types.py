"""Unit tests for semabridge.dax_translation.types — synthetic data only."""
import pytest

from semabridge.dax_translation.types import Dialect, TranslationRequest, TranslationResult


def _make_request(**overrides):
    defaults = dict(
        dax="SUM('SomeTable'[SomeColumn])",
        dataset_name="SomeTable",
        table_alias="sometable",
        dataset_col_lookup={"SomeTable": {"SOMECOLUMN"}},
        dataset_aliases={"SomeTable": "sometable"},
    )
    defaults.update(overrides)
    return TranslationRequest(**defaults)


def test_dialect_coerces_from_string():
    req = _make_request(dialect="snowflake")
    assert req.dialect is Dialect.SNOWFLAKE

    req2 = _make_request(dialect="databricks")
    assert req2.dialect is Dialect.DATABRICKS


def test_dialect_coerce_rejects_unknown_value():
    with pytest.raises(ValueError):
        _make_request(dialect="teradata")


def test_dialect_coerce_accepts_enum_directly():
    assert Dialect.coerce(Dialect.SNOWFLAKE) is Dialect.SNOWFLAKE


def test_request_defaults():
    req = _make_request()
    assert req.metric_name is None
    assert req.metrics_context == []
    assert req.dialect is Dialect.SNOWFLAKE


def test_dataset_col_lookup_and_aliases_are_required_positionally():
    # Required fields — omitting them is a TypeError, not a silent None.
    with pytest.raises(TypeError):
        TranslationRequest(
            dax="SUM('SomeTable'[SomeColumn])",
            dataset_name="SomeTable",
            table_alias="sometable",
        )


def test_translation_result_is_success_derived_from_sql():
    ok = TranslationResult(sql="SUM(sometable.\"SOMECOLUMN\"::FLOAT)", tier=1, original_dax="SUM('SomeTable'[SomeColumn])")
    assert ok.is_success is True

    failed = TranslationResult(sql=None, tier=4, original_dax="SomeUnsupportedDax()")
    assert failed.is_success is False


def test_translation_result_rejects_out_of_range_tier():
    with pytest.raises(ValueError):
        TranslationResult(sql="X", tier=0, original_dax="X")
    with pytest.raises(ValueError):
        TranslationResult(sql="X", tier=6, original_dax="X")


def test_translation_result_carries_provider_and_confidence_and_source_pipeline():
    result = TranslationResult(
        sql="SUM(sometable.\"SOMECOLUMN\"::FLOAT)",
        tier=5,
        original_dax="SUM('SomeTable'[SomeColumn])",
        provider="openai",
        translation_provider_confidence=0.82,
        source_pipeline="B",
    )
    assert result.provider == "openai"
    assert result.translation_provider_confidence == 0.82
    assert result.source_pipeline == "B"
    assert result.validation_notes == []
