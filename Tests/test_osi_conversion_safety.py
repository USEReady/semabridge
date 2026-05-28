import pytest
from semabridge.intermediate.models import OSIModel, OSIDataset
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.core.engine.exceptions import SemanticValidationError

def test_osimodel_empty_collection_safety():
    """Verify OSIModel defaults to empty lists for all semantic collections, not None."""
    model = OSIModel(unique_name="EmptyModel")
    assert model.metrics == []
    assert model.datasets == []
    assert model.tables == []  # Test property alias
    assert model.relationships == []
    assert model.dimensions == []

def test_osimodel_null_coercion():
    """Verify that explicitly passing None values for collections coercively overrides to empty lists."""
    model = OSIModel(
        unique_name="CoercedModel",
        datasets=None,
        metrics=None,
        relationships=None,
        dimensions=None
    )
    assert model.datasets == []
    assert model.metrics == []
    assert model.relationships == []
    assert model.dimensions == []
    assert model.tables == []

def test_osimodel_tables_property_alias():
    """Verify that the tables property successfully returns the datasets collection."""
    model = OSIModel(unique_name="PropertyModel")
    dataset = OSIDataset(unique_name="Sales")
    model.datasets.append(dataset)
    
    assert len(model.tables) == 1
    assert model.tables[0].unique_name == "Sales"
    assert model.tables == model.datasets

def test_converter_validation_guard():
    """Verify that the pre-conversion validation guard raises SemanticValidationError on invalid or malformed models."""
    converter = OSIToSMLConverter()
    
    # We bypass Pydantic validation by mutating the constructed object directly to None
    model = OSIModel(unique_name="InvalidModel")
    object.__setattr__(model, "metrics", None)
    
    with pytest.raises(SemanticValidationError) as excinfo:
        converter.from_osi(model)
        
    assert "metrics collection is None" in str(excinfo.value)

def test_converter_non_iterable_guard():
    """Verify that the converter raises SemanticValidationError when collections are non-iterable."""
    converter = OSIToSMLConverter()
    
    model = OSIModel(unique_name="MalformedModel")
    object.__setattr__(model, "metrics", 12345)  # non-iterable
    
    with pytest.raises(SemanticValidationError) as excinfo:
        converter.from_osi(model)
        
    assert "metrics is not iterable" in str(excinfo.value)

def test_converter_datasets_limit_guard():
    """Verify that exceeding architectural boundaries raises SemanticValidationError."""
    converter = OSIToSMLConverter()
    
    model = OSIModel(unique_name="HugeModel")
    # Simulate exceeding the 75 dataset limit by adding 76 mock datasets
    # (Pydantic mode="after" would also reject this, but validate_osi_model enforces it as well)
    for i in range(76):
        model.datasets.append(OSIDataset(unique_name=f"Table_{i}"))
        
    with pytest.raises(SemanticValidationError) as excinfo:
        converter.from_osi(model)
        
    assert "datasets collection exceeds limit of 75" in str(excinfo.value)

def test_converter_safe_empty_conversion():
    """Verify that an empty OSIModel converts to SMLModel safely without any iteration crashes."""
    converter = OSIToSMLConverter()
    model = OSIModel(unique_name="EmptyModel")
    
    sml = converter.from_osi(model)
    assert sml.unique_name == "EmptyModel"
    assert len(sml.datasets) == 1  # Note: calendar dimension is injected by default
    assert len(sml.metrics) == 0
    assert len(sml.relationships) == 0

def test_converter_metrics_context_propagation():
    """Verify that metrics_context propagates correctly, enabling resolution of nested measures."""
    from semabridge.intermediate.models import OSIMetric, OSIDataset, OSIColumn, OSIDataType
    converter = OSIToSMLConverter()
    
    model = OSIModel(unique_name="SalesModel")
    
    # 1. Add Fact dataset and columns
    cols = [OSIColumn(unique_name="Value", data_type=OSIDataType.DECIMAL)]
    dataset = OSIDataset(unique_name="Fact", columns=cols)
    model.datasets.append(dataset)
    
    # 2. Add base measure and nested/branching measure
    amount = OSIMetric(unique_name="Amount", expression="SUM([Value])", dataset="Fact")
    actual = OSIMetric(unique_name="Actual", expression="CALCULATE([Amount], Scenario[ScenarioDescription]=\"Actual\")", dataset="Fact")
    
    model.metrics.extend([amount, actual])
    
    # 3. Convert to SMLModel
    sml = converter.from_osi(model)
    
    # Amount is parsed, Actual resolves Amount through the passed metrics_context
    amount_sml = next(m for m in sml.metrics if m.unique_name == "Amount")
    actual_sml = next(m for m in sml.metrics if m.unique_name == "Actual")
    
    assert amount_sml.sql_expression.upper() == 'SUM(FACT."VALUE")'
    assert "SUM(FACT.\"VALUE\")" in actual_sml.sql_expression.upper()
