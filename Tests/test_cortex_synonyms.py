import pytest
import yaml

from semabridge.sml.models import (
    SMLModel,
    SMLDataset,
    SMLColumn,
    SMLDimension,
    SMLAttribute,
    SMLMetric,
    DataType,
    AggregationType,
)
from semabridge.intermediate.models import (
    OSIModel,
    OSIDataset,
    OSIColumn,
    OSIDimension,
    OSIAttribute,
    OSIMetric,
    OSIDataType,
    OSIAggregationType,
)
from semabridge.connectors.snowflake_emitter_parts.renderers import (
    generate_cortex_yaml,
    generate_cortex_yaml_from_osi,
)


class MockEmitter:
    """Mock emitter for testing rendering logic."""

    def __init__(self, enable_cortex=True):
        class Config:
            database = "TEST_DB"
            schema_name = "TEST_SCHEMA"

        class Behavior:
            class Features:
                enable_cortex_analyst = enable_cortex
            features = Features()

        self.config = Config()
        self.behavior = Behavior()

    def _safe_table_name(self, name):
        return name.upper()


class TestCortexAnalystSynonyms:
    """Test suite verifying synonyms rendering in Cortex Analyst YAML."""

    def test_sml_measure_and_dimension_synonyms(self):
        """Verify that SML metrics and dimensions synonyms are correctly rendered."""
        sml = SMLModel(
            unique_name="sales_model",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[
                        SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
                        SMLColumn(
                            unique_name="CustomerKey",
                            data_type=DataType.STRING,
                            synonyms=["client_id", "buyer_code"],
                        ),
                    ],
                )
            ],
            dimensions=[
                SMLDimension(
                    unique_name="Customer",
                    dataset="Sales",
                    attributes=[
                        SMLAttribute(
                            unique_name="CustomerKey",
                            dataset="Sales",
                            dataset_column="CustomerKey",
                        )
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="TotalRevenue",
                    dataset="Sales",
                    sql_expression="SUM(Sales.Revenue)",
                    aggregation=AggregationType.SUM,
                    synonyms=["Sales Revenue", "Gross Turnover"],
                )
            ],
        )

        emitter = MockEmitter()
        yaml_str = generate_cortex_yaml(emitter, sml)
        data = yaml.safe_load(yaml_str)

        tables = data["semantic_model"]["tables"]
        assert len(tables) == 1
        table = tables[0]

        # Verify dimension synonyms
        dims = table["dimensions"]
        assert len(dims) == 1
        assert dims[0]["name"] == "CustomerKey"
        assert dims[0]["synonyms"] == ["client_id", "buyer_code"]

        # Verify measure synonyms
        measures = table["measures"]
        assert len(measures) == 1
        assert measures[0]["name"] == "TotalRevenue"
        assert measures[0]["synonyms"] == ["Sales Revenue", "Gross Turnover"]

    def test_osi_measure_and_dimension_synonyms(self):
        """Verify that OSI metrics and dimensions synonyms are correctly rendered."""
        osi = OSIModel(
            unique_name="sales_model",
            datasets=[
                OSIDataset(
                    unique_name="Sales",
                    columns=[
                        # Note: OSIDataset.get_column matching is exact-case, so case-matching here
                        OSIColumn(unique_name="Revenue", data_type=OSIDataType.DECIMAL),
                        OSIColumn(
                            unique_name="CustomerKey",
                            data_type=OSIDataType.STRING,
                            synonyms=["client_id", "buyer_code"],
                        ),
                    ],
                )
            ],
            dimensions=[
                OSIDimension(
                    unique_name="Customer",
                    dataset="Sales",
                    attributes=[
                        OSIAttribute(
                            unique_name="CustomerKey",
                            dataset="Sales",
                            source_column="CustomerKey",
                        )
                    ],
                )
            ],
            metrics=[
                OSIMetric(
                    unique_name="TotalRevenue",
                    dataset="Sales",
                    sql_expression="SUM(Sales.Revenue)",
                    aggregation=OSIAggregationType.SUM,
                    synonyms=["Sales Revenue", "Gross Turnover"],
                )
            ],
        )

        emitter = MockEmitter()
        yaml_str = generate_cortex_yaml_from_osi(emitter, osi)
        data = yaml.safe_load(yaml_str)

        tables = data["semantic_model"]["tables"]
        assert len(tables) == 1
        table = tables[0]

        # Verify dimension synonyms
        dims = table["dimensions"]
        assert len(dims) == 1
        assert dims[0]["name"] == "CustomerKey"
        assert dims[0]["synonyms"] == ["client_id", "buyer_code"]

        # Verify measure synonyms
        measures = table["measures"]
        assert len(measures) == 1
        assert measures[0]["name"] == "TotalRevenue"
        assert measures[0]["synonyms"] == ["Sales Revenue", "Gross Turnover"]

    def test_empty_synonyms_omission(self):
        """Verify that synonyms field is omitted entirely (no empty lists or nulls) when empty."""
        sml = SMLModel(
            unique_name="sales_model",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[
                        SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
                        # Column synonyms is empty/absent
                        SMLColumn(unique_name="CustomerKey", data_type=DataType.STRING),
                    ],
                )
            ],
            dimensions=[
                SMLDimension(
                    unique_name="Customer",
                    dataset="Sales",
                    attributes=[
                        SMLAttribute(
                            unique_name="CustomerKey",
                            dataset="Sales",
                            dataset_column="CustomerKey",
                        )
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="TotalRevenue",
                    dataset="Sales",
                    sql_expression="SUM(Sales.Revenue)",
                    aggregation=AggregationType.SUM,
                    # metric synonyms is empty/absent
                )
            ],
        )

        emitter = MockEmitter()
        yaml_str = generate_cortex_yaml(emitter, sml)
        data = yaml.safe_load(yaml_str)

        table = data["semantic_model"]["tables"][0]

        # Verify synonyms key does not exist under dimension
        assert "synonyms" not in table["dimensions"][0]

        # Verify synonyms key does not exist under measure
        assert "synonyms" not in table["measures"][0]

        # Ensure that literally synonyms is omitted, and the YAML dump has no "synonyms: []" or "synonyms: null"
        assert "synonyms:" not in yaml_str

    def test_backward_compatibility(self):
        """Verify that YAML structure matches legacy formats when synonyms feature is not used."""
        sml = SMLModel(
            unique_name="sales_model",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[
                        SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="TotalRevenue",
                    dataset="Sales",
                    sql_expression="SUM(Sales.Revenue)",
                    aggregation=AggregationType.SUM,
                )
            ],
        )

        emitter = MockEmitter(enable_cortex=False)
        yaml_str = generate_cortex_yaml(emitter, sml)
        data = yaml.safe_load(yaml_str)

        assert data["semantic_model"]["node_type"] == "unknown"
        assert "synonyms" not in data["semantic_model"]["tables"][0]["measures"][0]


class TestCortexAnalystSampleValues:
    """Test suite verifying sample_values rendering in Cortex Analyst YAML."""

    def _sml(self, metric_source_column=None):
        return SMLModel(
            unique_name="sales_model",
            datasets=[
                SMLDataset(
                    unique_name="Sales",
                    columns=[
                        SMLColumn(unique_name="Revenue", data_type=DataType.DECIMAL),
                        SMLColumn(unique_name="CustomerKey", data_type=DataType.STRING),
                    ],
                )
            ],
            dimensions=[
                SMLDimension(
                    unique_name="Customer",
                    dataset="Sales",
                    attributes=[
                        SMLAttribute(
                            unique_name="CustomerKey",
                            dataset="Sales",
                            dataset_column="CustomerKey",
                        )
                    ],
                )
            ],
            metrics=[
                SMLMetric(
                    unique_name="TotalRevenue",
                    dataset="Sales",
                    sql_expression="SUM(Sales.Revenue)",
                    aggregation=AggregationType.SUM,
                    format_string="$#,##0.00",
                    source_column=metric_source_column,
                )
            ],
        )

    def test_sample_values_never_carries_format_string(self):
        """Regression: sample_values must never be the numeric display format."""
        emitter = MockEmitter()
        yaml_str = generate_cortex_yaml(emitter, self._sml())
        assert "Format:" not in yaml_str

        data = yaml.safe_load(yaml_str)
        measure = data["semantic_model"]["tables"][0]["measures"][0]
        assert "sample_values" not in measure or not str(
            measure.get("sample_values", "")
        ).startswith("Format:")

    def test_dimension_receives_real_sample_values_from_fetcher(self):
        """A dimension's sample_values come from the injected fetcher, not the model."""
        emitter = MockEmitter()

        def fake_fetcher(dataset_unique_name, column):
            if column == "CustomerKey":
                return ["C-1001", "C-1002"]
            return []

        yaml_str = generate_cortex_yaml(emitter, self._sml(), sample_fetcher=fake_fetcher)
        data = yaml.safe_load(yaml_str)
        dim = data["semantic_model"]["tables"][0]["dimensions"][0]
        assert dim["sample_values"] == ["C-1001", "C-1002"]

    def test_metric_with_source_column_receives_real_sample_values(self):
        """A metric that is a direct pass-through aggregation of one column is sampled."""
        emitter = MockEmitter()

        def fake_fetcher(dataset_unique_name, column):
            if column == "Revenue":
                return ["100.00", "250.50"]
            return []

        yaml_str = generate_cortex_yaml(
            emitter, self._sml(metric_source_column="Revenue"), sample_fetcher=fake_fetcher
        )
        data = yaml.safe_load(yaml_str)
        measure = data["semantic_model"]["tables"][0]["measures"][0]
        assert measure["sample_values"] == ["100.00", "250.50"]

    def test_metric_without_source_column_gets_no_sample_values(self):
        """A metric with no resolvable single source column gets no sample_values at all."""
        emitter = MockEmitter()

        def fake_fetcher(dataset_unique_name, column):
            return ["should never be used"]

        yaml_str = generate_cortex_yaml(emitter, self._sml(), sample_fetcher=fake_fetcher)
        data = yaml.safe_load(yaml_str)
        measure = data["semantic_model"]["tables"][0]["measures"][0]
        assert "sample_values" not in measure

    def test_empty_fetcher_result_is_handled_gracefully(self):
        """A table/column with zero rows or all-null values yields no error, no key."""
        emitter = MockEmitter()

        def empty_fetcher(dataset_unique_name, column):
            return []

        yaml_str = generate_cortex_yaml(
            emitter, self._sml(metric_source_column="Revenue"), sample_fetcher=empty_fetcher
        )
        data = yaml.safe_load(yaml_str)
        table = data["semantic_model"]["tables"][0]
        assert "sample_values" not in table["dimensions"][0]
        assert "sample_values" not in table["measures"][0]
