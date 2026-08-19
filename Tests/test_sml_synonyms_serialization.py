import pytest
from semabridge.sml.models import (
    SMLModel,
    SMLDataset,
    SMLColumn,
    SMLMetric,
    DataType,
    AggregationType,
)
from semabridge.sml.serializer import SMLSerializer as CoreSMLSerializer
from semabridge.formats.sml.serializer import SMLSerializer as FormatsSMLSerializer


def _create_test_model():
    column = SMLColumn(
        unique_name="revenue_col",
        label="Revenue Column",
        data_type=DataType.DECIMAL,
        synonyms=["rev", "income", "sales"]
    )
    dataset = SMLDataset(
        unique_name="sales_dataset",
        label="Sales Dataset",
        columns=[column]
    )
    metric = SMLMetric(
        unique_name="total_revenue",
        label="Total Revenue",
        dataset="sales_dataset",
        aggregation=AggregationType.SUM,
        source_column="revenue_col",
        synonyms=["total_sales", "overall_income"]
    )
    return SMLModel(
        unique_name="sales_model",
        label="Sales Model",
        datasets=[dataset],
        metrics=[metric]
    )


def test_core_sml_serializer_preserves_synonyms():
    model = _create_test_model()
    
    # Serialize to YAML
    yaml_content = CoreSMLSerializer.to_yaml(model)
    
    # Verify synonyms exist in serialized YAML
    assert "revenue_col" in yaml_content
    assert "income" in yaml_content
    assert "overall_income" in yaml_content
    
    # Deserialize back
    deserialized = CoreSMLSerializer.from_yaml(yaml_content)
    
    # Assert column synonyms are preserved
    col = deserialized.datasets[0].columns[0]
    assert col.unique_name == "revenue_col"
    assert set(col.synonyms) == {"rev", "income", "sales"}
    
    # Assert metric synonyms are preserved
    met = deserialized.metrics[0]
    assert met.unique_name == "total_revenue"
    assert set(met.synonyms) == {"total_sales", "overall_income"}


def test_formats_sml_serializer_preserves_synonyms():
    # SMLModel structures in formats/sml are dynamically validated.
    # SMLModel in formats/sml uses the exact same core models because formats.sml re-exports core sml models.
    model = _create_test_model()
    
    # Serialize to YAML
    yaml_content = FormatsSMLSerializer.to_yaml(model)
    
    # Verify synonyms exist in serialized YAML
    assert "revenue_col" in yaml_content
    assert "income" in yaml_content
    assert "overall_income" in yaml_content
    
    # Deserialize back
    deserialized = FormatsSMLSerializer.from_yaml(yaml_content)
    
    # Assert column synonyms are preserved
    col = deserialized.datasets[0].columns[0]
    assert col.unique_name == "revenue_col"
    assert set(col.synonyms) == {"rev", "income", "sales"}
    
    # Assert metric synonyms are preserved
    met = deserialized.metrics[0]
    assert met.unique_name == "total_revenue"
    assert set(met.synonyms) == {"total_sales", "overall_income"}


# ---------------------------------------------------------------------------
# Real incident: both serializers' hand-written _metric_to_dict/_dict_to_metric
# field lists had drifted behind SMLMetric's actual fields. complexity_tier
# and llm_self_reported_confidence (Tier-5 self-reported confidence, computed
# correctly in-memory right after OSIToSMLConverter.from_osi()) were silently
# dropped on every dry-run snapshot round-trip through EITHER serializer --
# confirmed live: a real dry-run API response showed llm_self_reported_
# confidence=null and complexity_tier=null for every metric, always,
# regardless of which tier actually resolved it.
# ---------------------------------------------------------------------------

def _metric_with_tier5_confidence():
    return SMLMetric(
        unique_name="llm_translated_metric",
        label="LLM Translated Metric",
        dataset="sales_dataset",
        aggregation=AggregationType.SUM,
        sql_expression="SUM(sales_dataset.revenue_col)",
        complexity_tier=5,
        llm_self_reported_confidence=0.82,
        validation_notes=["openai: confidence 0.40 below min_confidence 0.55 — rejected"],
        confidence=0.9,
        advisory_categories=["enrichment_column_unverifiable"],
        advisory_notes=["Cannot verify in dry-run — resolved by live enrichment at deploy time."],
    )


def _model_with_tier5_metric():
    column = SMLColumn(unique_name="revenue_col", label="Revenue Column", data_type=DataType.DECIMAL)
    dataset = SMLDataset(unique_name="sales_dataset", label="Sales Dataset", columns=[column])
    return SMLModel(
        unique_name="sales_model",
        label="Sales Model",
        datasets=[dataset],
        metrics=[_metric_with_tier5_confidence()],
    )


def test_core_sml_serializer_round_trips_complexity_tier_and_self_reported_confidence():
    model = _model_with_tier5_metric()
    yaml_content = CoreSMLSerializer.to_yaml(model)
    deserialized = CoreSMLSerializer.from_yaml(yaml_content)

    met = deserialized.metrics[0]
    assert met.complexity_tier == 5
    assert met.llm_self_reported_confidence == 0.82
    assert met.validation_notes == ["openai: confidence 0.40 below min_confidence 0.55 — rejected"]
    assert met.advisory_categories == ["enrichment_column_unverifiable"]
    assert met.advisory_notes == ["Cannot verify in dry-run — resolved by live enrichment at deploy time."]


def test_formats_sml_serializer_round_trips_complexity_tier_and_self_reported_confidence():
    """The formats/sml copy had drifted even further than the core one
    (it was also missing advisory_notes/advisory_categories in
    _dict_to_metric) -- this is the ACTUAL serializer
    repository/model_repository.py uses to persist a dry-run snapshot, so
    this is the copy that mattered for the real, live-observed bug."""
    model = _model_with_tier5_metric()
    yaml_content = FormatsSMLSerializer.to_yaml(model)
    deserialized = FormatsSMLSerializer.from_yaml(yaml_content)

    met = deserialized.metrics[0]
    assert met.complexity_tier == 5
    assert met.llm_self_reported_confidence == 0.82
    assert met.validation_notes == ["openai: confidence 0.40 below min_confidence 0.55 — rejected"]
    assert met.advisory_categories == ["enrichment_column_unverifiable"]
    assert met.advisory_notes == ["Cannot verify in dry-run — resolved by live enrichment at deploy time."]


# ---------------------------------------------------------------------------
# Real incident: both serializers' hand-written _column_to_dict/_dict_to_column
# field lists had drifted behind SMLColumn's actual fields the same way
# _metric_to_dict had -- synonym_sources and has_report_alias (among others)
# were silently dropped on every dry-run snapshot round-trip, for columns
# specifically, through EITHER serializer -- confirmed live: a real dry-run
# API response showed every column synonym as unattributable ("Unknown
# Source" in the UI) regardless of whether it was actually a genuine
# report-layer alias, a manual override, or auto-generated.
# ---------------------------------------------------------------------------

def _column_with_report_alias():
    return SMLColumn(
        unique_name="category_col",
        label="Category",
        data_type=DataType.STRING,
        synonyms=["Product Category"],
        synonym_sources={"Product Category": "report_alias"},
        has_report_alias=True,
    )


def _model_with_aliased_column():
    dataset = SMLDataset(unique_name="sales_dataset", columns=[_column_with_report_alias()])
    return SMLModel(unique_name="sales_model", datasets=[dataset])


def test_core_sml_serializer_round_trips_column_synonym_sources():
    model = _model_with_aliased_column()
    deserialized = CoreSMLSerializer.from_yaml(CoreSMLSerializer.to_yaml(model))

    col = deserialized.datasets[0].columns[0]
    assert col.synonyms == ["Product Category"]
    assert col.synonym_sources == {"Product Category": "report_alias"}
    assert col.has_report_alias is True


def test_formats_sml_serializer_round_trips_column_synonym_sources():
    """The formats/sml copy is the ACTUAL serializer
    repository/model_repository.py uses to persist a dry-run snapshot --
    this is the copy that mattered for the real, live-observed bug."""
    model = _model_with_aliased_column()
    deserialized = FormatsSMLSerializer.from_yaml(FormatsSMLSerializer.to_yaml(model))

    col = deserialized.datasets[0].columns[0]
    assert col.synonyms == ["Product Category"]
    assert col.synonym_sources == {"Product Category": "report_alias"}
    assert col.has_report_alias is True


def test_column_with_no_report_alias_round_trips_empty_provenance():
    """Negative control: a column with no detected report alias must still
    round-trip synonym_sources as {} and has_report_alias as False -- the
    fix must not fabricate provenance for fields that never had any."""
    column = SMLColumn(unique_name="plain_col", data_type=DataType.STRING, synonyms=["Alt Name"])
    dataset = SMLDataset(unique_name="sales_dataset", columns=[column])
    model = SMLModel(unique_name="m", datasets=[dataset])

    for serializer in (CoreSMLSerializer, FormatsSMLSerializer):
        deserialized = serializer.from_yaml(serializer.to_yaml(model))
        col = deserialized.datasets[0].columns[0]
        assert col.synonyms == ["Alt Name"]
        assert col.synonym_sources == {}
        assert col.has_report_alias is False


def test_tier_1_4_metric_has_no_self_reported_confidence_after_round_trip():
    """Negative control: a deterministic (non-Tier-5) metric must still
    round-trip as None/default -- the fix must not fabricate a value."""
    column = SMLColumn(unique_name="revenue_col", data_type=DataType.DECIMAL)
    dataset = SMLDataset(unique_name="sales_dataset", columns=[column])
    metric = SMLMetric(
        unique_name="deterministic_metric",
        dataset="sales_dataset",
        aggregation=AggregationType.SUM,
        sql_expression="SUM(sales_dataset.revenue_col)",
        complexity_tier=1,
    )
    model = SMLModel(unique_name="m", datasets=[dataset], metrics=[metric])

    for serializer in (CoreSMLSerializer, FormatsSMLSerializer):
        deserialized = serializer.from_yaml(serializer.to_yaml(model))
        met = deserialized.metrics[0]
        assert met.complexity_tier == 1
        assert met.llm_self_reported_confidence is None
        assert met.validation_notes == []
