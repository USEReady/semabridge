from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.intermediate.models import (
    OSIModel,
    OSIDataset,
    OSIColumn,
    OSIMetric,
)


def test_osi_to_sml_no_dax_translation_by_default():
    # Build a minimal OSI model containing one dataset and one metric
    ds = OSIDataset(unique_name="orders", columns=[OSIColumn(unique_name="id")])
    m = OSIMetric(unique_name="total_sales", label="Total Sales", dataset="orders", expression="SUM([Amount])")
    osi = OSIModel(unique_name="model1", label="Model 1", datasets=[ds], metrics=[m])

    conv = OSIToSMLConverter()
    sml = conv.from_osi(osi)

    assert len(sml.metrics) == 1
    metric = sml.metrics[0]
    # By default we do not perform DAX -> SQL translation, so sql_expression stays empty/None
    assert not getattr(metric, "sql_expression", None)
    # sync_enabled should be False by default when translation is disabled
    assert not metric.sync_enabled
