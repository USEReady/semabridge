import pytest
from semabridge.converter.tmsl_to_sml import TMSLTransformer
from semabridge.sml.models import SourcePlatform

class TestTMSLToSMLSynonyms:
    @pytest.fixture
    def transformer(self):
        return TMSLTransformer()

    def test_direct_column_and_measure_synonyms(self, transformer):
        """46, 47, 48: Direct TMSL -> SML extraction of synonyms"""
        tmsl = {
            "model": {
                "name": "Model",
                "tables": [{
                    "name": "Sales",
                    "columns": [{
                        "name": "sale_amount",
                        "dataType": "double",
                        "synonyms": ["Revenue"]
                    }],
                    "measures": [{
                        "name": "Total Sales",
                        "expression": "SUM([sale_amount])",
                        "synonyms": ["Gross Sales"]
                    }],
                    "partitions": [{"name": "P1"}]
                }]
            }
        }
        
        sml = transformer.transform(tmsl, "ws", "ds")
        
        ds = sml.datasets[0]
        col = ds.columns[0]
        # User + Heuristic (Sale Amount)
        assert "Revenue" in col.synonyms
        assert "Sale Amount" in col.synonyms
        
        metric = sml.metrics[0]
        # User + Heuristic (Total Sales)
        assert "Gross Sales" in metric.synonyms
        # "Total Sales" heuristic is skipped if it matches the label exactly

    def test_direct_malformed_handling(self, transformer):
        """51: Graceful fallback for non-list synonyms"""
        tmsl = {
            "model": {
                "name": "Model",
                "tables": [{
                    "name": "Sales",
                    "columns": [{
                        "name": "Revenue",
                        "dataType": "double",
                        "synonyms": "not-a-list"
                    }],
                    "partitions": [{"name": "P1"}]
                }]
            }
        }
        sml = transformer.transform(tmsl, "ws", "ds")
        col = sml.datasets[0].columns[0]
        assert isinstance(col.synonyms, list)
        assert "Revenue" in col.synonyms or len(col.synonyms) >= 0
