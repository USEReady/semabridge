import pytest
from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
from semabridge.intermediate.models import OSIModel, OSIDataset, OSIMetric, OSIDimension, OSIDataType, OSICardinality, OSIAggregationType
from semabridge.core.exceptions import ConversionError

class TestTMSLToOSI:
    
    @pytest.fixture
    def converter(self):
        return TMSLToOSIConverter()

    def test_basic_conversion(self, converter, sample_tmsl_json):
        """Test converting a basic TMSL structure to OSI."""
        source = {
            "tmsl": sample_tmsl_json,
            "workspace_id": "ws-123",
            "dataset_id": "ds-456"
        }
        
        osi = converter.to_osi(source)
        
        # 1. Check Model Meta
        assert isinstance(osi, OSIModel)
        assert osi.unique_name == "ds-456"
        assert osi.label == "SalesModel"
        assert osi.metadata["workspace_id"] == "ws-123"
        assert osi.source_platform == "fabric"
        
        # 2. Check Datasets
        assert len(osi.datasets) == 2
        
        sales_ds = next(d for d in osi.datasets if d.unique_name == "Sales")
        assert len(sales_ds.columns) == 3
        
        # Check column type logic
        rev_col = next(c for c in sales_ds.columns if c.unique_name == "Revenue")
        assert rev_col.data_type == OSIDataType.FLOAT # double -> float (or decimal if mapped so)
        
        qty_col = next(c for c in sales_ds.columns if c.unique_name == "Quantity")
        assert qty_col.data_type == OSIDataType.INTEGER # int64
        
        # A table literally named "Table" (Power BI's default generic name for
        # an auto-imported/unrenamed table) must resolve source_table to its
        # own name like any other table — there is no general signal in the
        # TMSL for what a "better" name would be, so guessing one (the
        # converter used to hardcode "DEVICE_INVENTORY" here) silently
        # corrupts any other customer's model that has a genuine "Table".
        ds_t = converter._parse_dataset({"name": "Table"})
        assert ds_t.source_table == "Table"

        # 3. Check Metrics
        assert len(osi.metrics) == 1
        metric = osi.metrics[0]
        assert metric.unique_name == "Total Revenue"
        assert metric.dataset == "Sales"
        assert metric.expression == "SUM([Revenue])"

        # 4. Check Relationships
        assert len(osi.relationships) == 1
        rel = osi.relationships[0]
        assert rel.from_dataset == "Sales"
        assert rel.to_dataset == "Customer"
        assert rel.cardinality == OSICardinality.MANY_TO_ONE
        
        # 5. Check Dimensions (Implicit creation)
        assert len(osi.dimensions) == 2
        dim_sales = next(d for d in osi.dimensions if d.unique_name == "Sales")
        assert dim_sales is not None
        assert len(dim_sales.attributes) == 3

    def test_missing_input(self, converter):
        """Test error handling for missing inputs."""
        with pytest.raises(ConversionError) as exc:
            converter.to_osi({})
        assert "Missing 'tmsl' or 'dataset_id'" in str(exc.value)

    def test_malformed_tmsl(self, converter):
        """Test specific malformed input cases (if any specific checks exist)."""
        source = {
            "tmsl": {"model": {"tables": [{"name": "BadTable", "columns": "invalid_list"}]}}, 
            "workspace_id": "ws",
            "dataset_id": "ds"
        }
        # This will likely raise a KeyError or TypeError inside _parse_column iteration
        # Ensure it is wrapped in ConversionError
        with pytest.raises(ConversionError):
            converter.to_osi(source)

    def test_extended_fabric_datatypes_map_to_osi(self, converter):
        """Ensure common Fabric type variants map to non-string OSI datatypes."""
        source = {
            "tmsl": {
                "model": {
                    "name": "TypedModel",
                    "tables": [{
                        "name": "TypedTable",
                        "columns": [
                            {"name": "OrderDate", "dataType": "Date"},
                            {"name": "EventTime", "dataType": "Time"},
                            {"name": "IsActive", "dataType": "Bool"},
                            {"name": "Amount", "dataType": "Currency"},
                        ],
                    }],
                }
            },
            "workspace_id": "ws-123",
            "dataset_id": "ds-typed",
        }

        osi = converter.to_osi(source)
        ds = next(d for d in osi.datasets if d.unique_name == "TypedTable")

        assert next(c for c in ds.columns if c.unique_name == "OrderDate").data_type == OSIDataType.DATE
        assert next(c for c in ds.columns if c.unique_name == "EventTime").data_type == OSIDataType.TIME
        assert next(c for c in ds.columns if c.unique_name == "IsActive").data_type == OSIDataType.BOOLEAN
        assert next(c for c in ds.columns if c.unique_name == "Amount").data_type == OSIDataType.DECIMAL

    def test_blank_measure_is_retained(self, converter):
        """Blank/error-state measures should still appear in OSI output."""
        source = {
            "tmsl": {
                "model": {
                    "name": "BrokenMeasureModel",
                    "tables": [{
                        "name": "Sales",
                        "columns": [],
                        "measures": [{
                            "name": "Broken Measure",
                            "expression": "",
                        }],
                    }],
                }
            },
            "workspace_id": "ws-123",
            "dataset_id": "ds-broken",
        }

        osi = converter.to_osi(source)

        assert len(osi.metrics) == 1
        metric = osi.metrics[0]
        assert metric.unique_name == "Broken Measure"
        assert metric.expression == "[Broken Measure]"

    def test_summarize_by_columns_become_metrics_not_dimensions(self, converter):
        """Fabric Aggregation columns should emit as metrics and be excluded from dimensions."""
        source = {
            "tmsl": {
                "model": {
                    "name": "AggregationModel",
                    "tables": [{
                        "name": "SalesFact",
                        "columns": [
                            {"name": "ProductID", "dataType": "int64", "summarizeBy": "none"},
                            {"name": "Units", "dataType": "int64", "summarizeBy": "sum"},
                            {"name": "Revenue", "dataType": "double", "summarizeBy": "sum"},
                            {"name": "Score", "dataType": "double", "summarizeBy": "average"},
                        ],
                    }],
                }
            },
            "workspace_id": "ws-123",
            "dataset_id": "ds-agg",
        }

        osi = converter.to_osi(source)
        ds = next(d for d in osi.datasets if d.unique_name == "SalesFact")
        units_col = next(c for c in ds.columns if c.unique_name == "Units")

        assert units_col.is_measure_candidate is True
        assert units_col.default_aggregation == OSIAggregationType.SUM

        metric_by_name = {m.unique_name: m for m in osi.metrics}
        assert metric_by_name["Units"].source_column == "Units"
        assert metric_by_name["Units"].aggregation == OSIAggregationType.SUM
        assert metric_by_name["Revenue"].source_column == "Revenue"
        assert metric_by_name["Score"].aggregation == OSIAggregationType.AVG

        dim = next(d for d in osi.dimensions if d.unique_name == "SalesFact")
        assert [attr.unique_name for attr in dim.attributes] == ["ProductID"]

    def test_skip_auto_hidden_date_tables(self, converter):
        """Auto-generated Power BI date tables should be skipped in OSI conversion."""
        source = {
            "tmsl": {
                "model": {
                    "name": "DateModel",
                    "tables": [
                        {"name": "Sales", "columns": [{"name": "Id", "dataType": "int64"}]},
                        {"name": "LocalDateTable_123", "isHidden": True, "columns": []},
                        {"name": "DateTableTemplate_abc", "isHidden": True, "columns": []},
                    ],
                }
            },
            "workspace_id": "ws-123",
            "dataset_id": "ds-date",
        }

        osi = converter.to_osi(source)

        assert [ds.unique_name for ds in osi.datasets] == ["Sales"]


class TestPBIXAliasOSIPropagation:
    """Test suite for PBIX report aliases propagation to OSI metric synonyms."""

    @pytest.fixture
    def converter(self):
        return TMSLToOSIConverter()

    def test_existing_synonyms_and_pbix_aliases_merge(self, converter):
        """Verify that PBIX report aliases augment existing synonyms, preserving order and removing duplicates."""
        source = {
            "tmsl": {
                "model": {
                    "name": "SalesModel",
                    "tables": [{
                        "name": "SalesTable",
                        "measures": [{
                            "name": "Revenue",
                            "expression": "SUM(Sales[Amount])",
                            "synonyms": ["Sales Revenue"]
                        }],
                    }],
                }
            },
            "workspace_id": "ws-123",
            "dataset_id": "ds-123",
            "field_aliases": [
                {
                    "field": "Revenue",
                    "field_type": "measure",
                    "aliases": [
                        "Total Revenue",
                        "Sales Revenue",   # Duplicate, should be deduplicated
                        "revenue",         # Case-insensitive match to name, should be excluded
                        "Monthly Revenue"
                    ]
                }
            ]
        }

        osi = converter.to_osi(source)
        assert len(osi.metrics) == 1
        metric = osi.metrics[0]
        # Auto-generated synonyms will be merged as well, but the matched report aliases
        # must be appended following existing synonyms (UI/user/auto).
        assert "Sales Revenue" in metric.synonyms
        assert "Total Revenue" in metric.synonyms
        assert "Monthly Revenue" in metric.synonyms
        assert "revenue" not in metric.synonyms
        assert "Revenue" not in metric.synonyms

        # Discovery order preservation check: total revenue should precede monthly revenue
        tr_idx = metric.synonyms.index("Total Revenue")
        mr_idx = metric.synonyms.index("Monthly Revenue")
        assert tr_idx < mr_idx

    def test_exact_matching_only(self, converter):
        """Verify that matching uses case-insensitive exact equality only, no substrings/fuzzy matching."""
        source = {
            "tmsl": {
                "model": {
                    "name": "SalesModel",
                    "tables": [{
                        "name": "SalesTable",
                        "measures": [
                            {"name": "Revenue", "expression": "1"},
                            {"name": "Revenue Forecast", "expression": "2"}
                        ],
                    }],
                }
            },
            "workspace_id": "ws-123",
            "dataset_id": "ds-123",
            "field_aliases": [
                {
                    "field": "Revenue",
                    "field_type": "measure",
                    "aliases": ["Total Income"]
                }
            ]
        }

        osi = converter.to_osi(source)
        metrics = {m.unique_name: m for m in osi.metrics}
        assert "Total Income" in metrics["Revenue"].synonyms
        assert "Total Income" not in metrics["Revenue Forecast"].synonyms
        assert metrics["Revenue"].has_report_alias is True
        assert metrics["Revenue"].synonym_sources["Total Income"] == "report_alias"
        assert metrics["Revenue Forecast"].has_report_alias is False

    def test_empty_field_aliases(self, converter):
        """Verify legacy synonym generation is unaffected when no field aliases are supplied."""
        source = {
            "tmsl": {
                "model": {
                    "name": "SalesModel",
                    "tables": [{
                        "name": "SalesTable",
                        "measures": [{
                            "name": "TotalRevenue",
                            "expression": "1",
                            "synonyms": ["Sales Revenue"]
                        }],
                    }],
                }
            },
            "workspace_id": "ws-123",
            "dataset_id": "ds-123",
        }

        osi = converter.to_osi(source)
        metric = osi.metrics[0]
        # Verify user synonym "Sales Revenue" and auto-generated "Total Revenue" are present
        assert "Sales Revenue" in metric.synonyms
        assert "Total Revenue" in metric.synonyms
        assert metric.has_report_alias is False
        assert metric.synonym_sources["Sales Revenue"] == "tmsl_authored"
        assert metric.synonym_sources["Total Revenue"] == "auto_generated"

    def test_multiple_measures_mapping(self, converter):
        """Verify report aliases are correctly mapped to their respective target measures."""
        source = {
            "tmsl": {
                "model": {
                    "name": "SalesModel",
                    "tables": [{
                        "name": "SalesTable",
                        "measures": [
                            {"name": "SalesCount", "expression": "1"},
                            {"name": "Profit", "expression": "2"}
                        ],
                    }],
                }
            },
            "workspace_id": "ws-123",
            "dataset_id": "ds-123",
            "field_aliases": [
                {"field": "SalesCount", "field_type": "measure", "aliases": ["Transaction Count"]},
                {"field": "Profit", "field_type": "measure", "aliases": ["Net Profit"]}
            ]
        }

        osi = converter.to_osi(source)
        metrics = {m.unique_name: m for m in osi.metrics}
        assert "Transaction Count" in metrics["SalesCount"].synonyms
        assert "Net Profit" not in metrics["SalesCount"].synonyms
        assert "Net Profit" in metrics["Profit"].synonyms
        assert "Transaction Count" not in metrics["Profit"].synonyms

    def test_column_exact_matching_only(self, converter):
        """Verify column report-alias matching uses case-insensitive exact
        equality only (no substrings/fuzzy matching), mirroring
        test_exact_matching_only but for the column path."""
        source = {
            "tmsl": {
                "model": {
                    "name": "GenericModel",
                    "tables": [{
                        "name": "GenericTable",
                        "columns": [
                            {"name": "GenericField", "dataType": "string"},
                            {"name": "GenericField Extended", "dataType": "string"},
                        ],
                    }],
                }
            },
            "workspace_id": "ws-x", "dataset_id": "ds-x",
            "field_aliases": [
                {"field": "GenericField", "field_type": "column", "table": "GenericTable", "aliases": ["Generic Display Label"]}
            ],
        }

        osi = converter.to_osi(source)
        columns = {c.unique_name: c for ds in osi.datasets for c in ds.columns}
        assert "Generic Display Label" in columns["GenericField"].synonyms
        assert columns["GenericField"].has_report_alias is True
        assert columns["GenericField"].synonym_sources["Generic Display Label"] == "report_alias"
        assert "Generic Display Label" not in columns["GenericField Extended"].synonyms
        assert columns["GenericField Extended"].has_report_alias is False

    def test_column_no_alias_falls_back_cleanly(self, converter):
        """A column never visualized (no field_aliases entry) gets
        has_report_alias=False and synonyms unaffected — never null, never
        an error. Uses a single-token lowercase name so the mechanical
        auto-synonym generator (a separate, unrelated synonym source) has
        no PascalCase/snake_case boundary to split, keeping this test
        focused purely on report-alias fallback behavior."""
        source = {
            "tmsl": {
                "model": {
                    "name": "GenericModel",
                    "tables": [{
                        "name": "GenericTable",
                        "columns": [{"name": "unvisualizedfield", "dataType": "string"}],
                    }],
                }
            },
            "workspace_id": "ws-x", "dataset_id": "ds-x",
            "field_aliases": [],
        }

        osi = converter.to_osi(source)
        col = osi.datasets[0].columns[0]
        assert col.has_report_alias is False
        assert col.synonyms == []
        assert col.synonym_sources == {}

    def test_same_name_measure_and_column_do_not_cross_contaminate(self, converter):
        """A measure and a column sharing a name (legal per TOM — measure
        names are global, column names are per-table) must each resolve
        only their own report-layer aliases, never the other's, never a
        merged union."""
        source = {
            "tmsl": {"model": {"name": "GenericModel", "tables": [
                {
                    "name": "TableA",
                    "columns": [{"name": "sharedname", "dataType": "string"}],
                },
                {
                    "name": "TableB",
                    "measures": [{"name": "sharedname", "expression": "1"}],
                },
            ]}},
            "workspace_id": "ws-x",
            "dataset_id": "ds-x",
            "field_aliases": [
                {"field": "sharedname", "field_type": "column", "table": "TableA", "aliases": ["Column Alias Label"]},
                {"field": "sharedname", "field_type": "measure", "aliases": ["Measure Alias Label"]},
            ],
        }
        osi = converter.to_osi(source)

        column = next(c for ds in osi.datasets for c in ds.columns if c.unique_name == "sharedname")
        metric = next(m for m in osi.metrics if m.unique_name == "sharedname")

        assert column.synonyms == ["Column Alias Label"]
        assert column.has_report_alias is True
        assert "Measure Alias Label" not in column.synonyms

        assert metric.synonyms == ["Measure Alias Label"]
        assert metric.has_report_alias is True
        assert "Column Alias Label" not in metric.synonyms

    def test_same_column_name_different_tables_do_not_cross_contaminate(self, converter):
        """Two tables each with a column of the same name (legal — TMSL only
        requires column-name uniqueness within a table) must each resolve
        only their own table's report-layer aliases."""
        source = {
            "tmsl": {"model": {"name": "GenericModel", "tables": [
                {"name": "TableA", "columns": [{"name": "sharedname", "dataType": "string"}]},
                {"name": "TableB", "columns": [{"name": "sharedname", "dataType": "string"}]},
            ]}},
            "workspace_id": "ws-x", "dataset_id": "ds-x",
            "field_aliases": [
                {"field": "sharedname", "field_type": "column", "table": "TableA", "aliases": ["Alias For A"]},
                {"field": "sharedname", "field_type": "column", "table": "TableB", "aliases": ["Alias For B"]},
            ],
        }
        osi = converter.to_osi(source)
        col_a = next(c for ds in osi.datasets if ds.unique_name == "TableA" for c in ds.columns)
        col_b = next(c for ds in osi.datasets if ds.unique_name == "TableB" for c in ds.columns)

        assert col_a.synonyms == ["Alias For A"] and "Alias For B" not in col_a.synonyms
        assert col_b.synonyms == ["Alias For B"] and "Alias For A" not in col_b.synonyms


class TestSynonymProvenance:
    """Test suite for synonym_sources provenance tracking (distinguishing
    genuine report-layer aliases from auto-generated/TMSL-authored/manual
    synonyms that can coincidentally look similar)."""

    @pytest.fixture
    def converter(self):
        return TMSLToOSIConverter()

    def test_synonym_sources_distinguish_provenance(self, converter):
        """A field with both an auto-generated guess and a genuine report
        alias must have each synonym tagged with its true origin, not merged
        into an indistinguishable flat list."""
        source = {
            "tmsl": {"model": {"name": "GenericModel", "tables": [{
                "name": "GenericTable",
                "measures": [{"name": "GenericCamelCaseMeasure", "expression": "1"}],
            }]}},
            "workspace_id": "ws-x", "dataset_id": "ds-x",
            "field_aliases": [{"field": "GenericCamelCaseMeasure", "field_type": "measure",
                                "aliases": ["Genuine Report Title"]}],
        }
        osi = converter.to_osi(source)
        metric = osi.metrics[0]
        assert "Genuine Report Title" in metric.synonyms
        assert metric.synonym_sources["Genuine Report Title"] == "report_alias"
        # Auto-generated split of "GenericCamelCaseMeasure" (if any) must be
        # tagged distinctly from the genuine report alias.
        for syn, tag in metric.synonym_sources.items():
            if syn != "Genuine Report Title":
                assert tag != "report_alias"

    def test_auto_generated_only_synonym_not_labeled_as_report_alias(self, converter):
        """A field with zero report-layer match must have has_report_alias=False
        and every synonym tagged as something other than 'report_alias'."""
        source = {
            "tmsl": {"model": {"name": "GenericModel", "tables": [{
                "name": "GenericTable",
                "measures": [{"name": "SomeCamelCaseMeasure", "expression": "1"}],
            }]}},
            "workspace_id": "ws-x", "dataset_id": "ds-x",
            "field_aliases": [],
        }
        osi = converter.to_osi(source)
        metric = osi.metrics[0]
        assert metric.has_report_alias is False
        assert all(tag != "report_alias" for tag in metric.synonym_sources.values())

    def test_manual_and_tmsl_authored_synonyms_tagged_distinctly(self, converter):
        """UI-override and TMSL-authored synonyms must be tagged with their
        own distinct provenance, not lumped together or mislabeled."""
        source = {
            "tmsl": {"model": {"name": "GenericModel", "tables": [{
                "name": "GenericTable",
                "measures": [{
                    "name": "GenericMeasure",
                    "expression": "1",
                    "synonyms": ["Authored In TMSL"],
                }],
            }]}},
            "workspace_id": "ws-x", "dataset_id": "ds-x",
        }
        osi = converter.to_osi(source)
        metric = osi.metrics[0]
        assert metric.synonym_sources["Authored In TMSL"] == "tmsl_authored"
        assert metric.has_report_alias is False

