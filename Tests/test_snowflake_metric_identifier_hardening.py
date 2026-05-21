from types import SimpleNamespace

from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
from semabridge.utils.identifiers import IdentifierSanitizer


def _build_metrics_builder() -> MetricsClauseBuilder:
    sanitizer = IdentifierSanitizer()
    return MetricsClauseBuilder(
        identifier_sanitizer=sanitizer,
        schema_manager=SimpleNamespace(),
        sanitizer=SimpleNamespace(),
        translator=SimpleNamespace(),
        config=SimpleNamespace(),
    )


def test_rewrite_raw_metric_references_for_divide_style_expressions() -> None:
    builder = _build_metrics_builder()
    model = SimpleNamespace(
        metrics=[
            SimpleNamespace(unique_name="Var LE1"),
            SimpleNamespace(unique_name="LE1"),
            SimpleNamespace(unique_name="Gross Margin"),
            SimpleNamespace(unique_name="Total Revenue"),
            SimpleNamespace(unique_name="Total VanArsdel / Total"),
        ]
    )

    expr = "COALESCE((Var LE1) / NULLIF((LE1), 0), BLANK())"
    rewritten = builder._rewrite_raw_metric_references(expr, model)

    assert "Var LE1" not in rewritten
    assert '"VAR_LE1"' in rewritten
    assert "(LE1)" in rewritten

    expr2 = "COALESCE((Gross Margin) / NULLIF((Total Revenue), 0), 0)"
    rewritten2 = builder._rewrite_raw_metric_references(expr2, model)

    assert "Gross Margin" not in rewritten2
    assert "Total Revenue" not in rewritten2
    assert '"GROSS_MARGIN"' in rewritten2
    assert '"TOTAL_REVENUE"' in rewritten2


def test_identifier_sanitizer_truncates_to_snowflake_limit() -> None:
    sanitizer = IdentifierSanitizer(max_identifier_length=255)

    very_long_name = "Severity Groups " + ("alpha beta gamma delta " * 30)
    first = sanitizer.sanitize_column(very_long_name)
    second = sanitizer.sanitize_column(very_long_name)

    assert len(first) <= 255
    assert first == second
    assert first[-9] == "_"
