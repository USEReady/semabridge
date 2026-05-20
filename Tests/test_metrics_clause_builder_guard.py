from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder


def test_metric_expression_normalization_strips_backticks():
    normalized = MetricsClauseBuilder._normalize_snowflake_metric_expression("SUM(`REVENUE`)")

    assert normalized == "SUM(REVENUE)"
