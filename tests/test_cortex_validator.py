"""Tests for Mandate 6: Cortex Validator — semantic model grounding."""

import pytest
from semabridge.core.validation.cortex_validator import CortexValidator, CortexValidationReport


@pytest.fixture
def valid_cortex_yaml():
    return {
        "name": "TestSemanticModel",
        "description": "A test semantic model for Cortex Analyst",
        "tables": [
            {
                "name": "SALES",
                "description": "Sales fact table",
                "primary_key": ["ORDER_ID"],
                "columns": [
                    {"name": "ORDER_ID", "description": "Unique order identifier"},
                    {"name": "REVENUE", "description": "Revenue amount"},
                    {"name": "ORDER_DATE", "description": "Date of order"},
                ],
                "metrics": [
                    {
                        "name": "TOTAL_REVENUE",
                        "expression": 'SUM("REVENUE")',
                    },
                ],
            },
        ],
        "verified_queries": [
            {
                "question": "What is total revenue?",
                "sql": 'SELECT SUM("REVENUE") FROM SALES',
            },
        ],
    }


@pytest.fixture
def minimal_cortex_yaml():
    return {
        "name": "MinimalModel",
        "tables": [
            {
                "name": "DATA",
                "columns": [
                    {"name": "ID"},
                    {"name": "VALUE"},
                ],
            },
        ],
    }


class TestCortexValidatorValid:
    """Test validation of a well-formed Cortex YAML."""

    def test_no_errors(self, valid_cortex_yaml):
        validator = CortexValidator(require_descriptions=True)
        report = validator.validate(valid_cortex_yaml)
        assert not report.has_errors

    def test_has_info(self, valid_cortex_yaml):
        validator = CortexValidator(require_descriptions=True)
        report = validator.validate(valid_cortex_yaml)
        assert len(report.info) > 0

    def test_summary_format(self, valid_cortex_yaml):
        validator = CortexValidator()
        report = validator.validate(valid_cortex_yaml)
        assert "error(s)" in report.summary()
        assert "warning(s)" in report.summary()


class TestCortexValidatorMissingDescriptions:
    """Test description completeness checks."""

    def test_missing_descriptions_error(self, minimal_cortex_yaml):
        validator = CortexValidator(require_descriptions=True)
        report = validator.validate(minimal_cortex_yaml)
        assert report.has_errors
        desc_errors = [e for e in report.errors if "description" in e.lower()]
        assert len(desc_errors) >= 1

    def test_missing_descriptions_warning_only(self, minimal_cortex_yaml):
        validator = CortexValidator(require_descriptions=False)
        report = validator.validate(minimal_cortex_yaml)
        # Should be warnings, not errors
        desc_errors = [e for e in report.errors if "description" in e.lower()]
        assert len(desc_errors) == 0
        desc_warnings = [w for w in report.warnings if "description" in w.lower()]
        assert len(desc_warnings) >= 1


class TestCortexValidatorEmpty:
    """Test edge cases."""

    def test_empty_yaml(self):
        validator = CortexValidator()
        report = validator.validate({})
        assert report.has_errors

    def test_no_tables(self):
        validator = CortexValidator()
        report = validator.validate({"name": "Test"})
        assert report.has_errors

    def test_no_verified_queries_warns(self, valid_cortex_yaml):
        del valid_cortex_yaml["verified_queries"]
        validator = CortexValidator()
        report = validator.validate(valid_cortex_yaml)
        vq_warnings = [w for w in report.warnings if "verified" in w.lower()]
        assert len(vq_warnings) >= 1


class TestCortexValidatorNaming:
    """Test identifier naming checks."""

    def test_reserved_word_column(self):
        data = {
            "name": "TestModel",
            "description": "test",
            "tables": [
                {
                    "name": "ORDERS",
                    "description": "orders table",
                    "columns": [
                        {"name": "SELECT", "description": "reserved word col"},
                    ],
                },
            ],
        }
        validator = CortexValidator()
        report = validator.validate(data)
        naming_warnings = [w for w in report.warnings if "reserved" in w.lower()]
        assert len(naming_warnings) >= 1
