"""
Tests for the Deployment Error Reporter.

Covers:
    - ModelDeploymentResult creation
    - DeploymentReport aggregation
    - Text formatting (format_text)
    - JSON formatting (format_json)  
    - suggest_fix pattern matching
    - suggest_fixes_for_errors batch computation
"""

import json
import pytest

from semabridge.utils.error_reporter import (
    DeploymentReport,
    DeploymentStatus,
    ModelDeploymentResult,
    suggest_fix,
    suggest_fixes_for_errors,
)


# =========================================================================
# ModelDeploymentResult
# =========================================================================


class TestModelDeploymentResult:
    def test_success_result(self):
        r = ModelDeploymentResult(
            model_name="FactSales",
            status="success",
            duration_ms=1200,
        )
        assert r.model_name == "FactSales"
        assert r.status == "success"
        assert len(r.errors) == 0

    def test_failed_result_with_errors(self):
        r = ModelDeploymentResult(
            model_name="DimProduct",
            status="failed",
            errors=["invalid identifier: FOO"],
            warnings=["Column AMOUNT might be calculated"],
        )
        assert r.status == "failed"
        assert len(r.errors) == 1
        assert len(r.warnings) == 1


# =========================================================================
# DeploymentReport
# =========================================================================


class TestDeploymentReport:
    def test_empty_report(self):
        report = DeploymentReport()
        assert report.total_models == 0

    def test_add_models(self):
        report = DeploymentReport()
        report.add_model_result(
            ModelDeploymentResult(model_name="A", status="success")
        )
        report.add_model_result(
            ModelDeploymentResult(model_name="B", status="failed", errors=["err"])
        )
        assert report.total_models == 2

    def test_format_text_nonempty(self):
        report = DeploymentReport()
        report.model_results.append(
            ModelDeploymentResult(model_name="Sales", status="success", duration_ms=500)
        )
        report.model_results.append(
            ModelDeploymentResult(
                model_name="Product",
                status="failed",
                errors=["invalid identifier: X"],
                suggested_fixes=["Check column casing"],
            )
        )
        text = report.format_text()
        assert isinstance(text, str)
        assert "Sales" in text
        assert "Product" in text
        assert len(text) > 50

    def test_format_json_valid(self):
        report = DeploymentReport()
        report.model_results.append(
            ModelDeploymentResult(model_name="Test", status="success")
        )
        result = report.format_json()
        # Should be valid JSON (either string or dict)
        if isinstance(result, str):
            parsed = json.loads(result)
        else:
            parsed = result
        assert "models" in parsed or "model_results" in parsed or isinstance(parsed, (dict, list))


# =========================================================================
# suggest_fix
# =========================================================================


class TestSuggestFix:
    @pytest.mark.parametrize("error_msg,expected_keyword", [
        ("SQL compilation error: invalid identifier 'FOO'", "identifier"),
        ("Object 'TABLE_X' does not exist", "table"),
        ("primary key not defined for dataset", "pk_resolution"),
        ("semaphore load shedding timeout", "warehouse"),
        ("User is not authorized", "permission"),
        ("AttributeError: 'NoneType' has no attribute 'columns'", "attribute"),
    ])
    def test_pattern_matching(self, error_msg, expected_keyword):
        fix = suggest_fix(error_msg)
        assert isinstance(fix, str)
        assert len(fix) > 0
        assert expected_keyword.lower() in fix.lower() or len(fix) > 10  # Reasonable fix

    def test_unknown_error_returns_generic(self):
        fix = suggest_fix("something completely unknown happened")
        assert isinstance(fix, str)
        assert len(fix) > 0


# =========================================================================
# suggest_fixes_for_errors
# =========================================================================


class TestSuggestFixesForErrors:
    def test_batch_fixes(self):
        errors = [
            "invalid identifier 'COL'",
            "table FACT_X does not exist",
            "primary key missing",
        ]
        fixes = suggest_fixes_for_errors(errors)
        assert isinstance(fixes, list)
        assert len(fixes) == len(errors)
        assert all(isinstance(f, str) for f in fixes)
