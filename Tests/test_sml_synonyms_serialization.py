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
