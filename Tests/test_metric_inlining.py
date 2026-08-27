import pytest
from semabridge.connectors.metrics_clause_builder import MetricsClauseBuilder
from semabridge.utils.identifiers import IdentifierSanitizer
from semabridge.connectors.semantic_ddl_sanitizer import SemanticDDLSanitizer
from semabridge.core.drop_ledger import DropLedger, DropStage

class _DummySchema:
    def _resolve_physical_column_name(self, dataset, column: str, model=None) -> str:
        return IdentifierSanitizer().sanitize_column(column)


def test_metric_inlining_strips_inner_synonyms_clause():
    """Safeguard A: Inlined referenced metric's WITH SYNONYMS clause must be stripped."""
    id_sanitizer = IdentifierSanitizer()
    sanitizer = SemanticDDLSanitizer(id_sanitizer)
    drop_ledger = DropLedger()
    builder = MetricsClauseBuilder(
        id_sanitizer,
        _DummySchema(),
        sanitizer,
        translator=None,
        config=None,
        drop_ledger=drop_ledger,
    )

    metrics_lines = [
        "  SALESFACT.\"PCT_CATEGORY_COMPETE_SHARE\" AS FLOOR(DIV0(SUM(SALESFACT.UNITS), SUM(SALESFACT.TOTAL)) * 100) WITH SYNONYMS = ('compete_share', 'compete_pct')",
        "  SALESFACT.\"ATINDICATOR01\" AS CASE WHEN SALESFACT.\"PCT_CATEGORY_COMPETE_SHARE\" < 0.55 THEN 1 ELSE 2 END",
    ]
    metric_name_set = {"PCT_CATEGORY_COMPETE_SHARE", "ATINDICATOR01"}

    pruned = builder._prune_unresolved_metric_lines(metrics_lines, metric_name_set)
    at_line = [l for l in pruned if "ATINDICATOR01" in l][0]

    # Must inline the formula
    assert "FLOOR(DIV0(SUM(SALESFACT.UNITS)" in at_line
    # Must NOT contain inner WITH SYNONYMS text in the inlined expression body
    assert "WITH SYNONYMS = ('compete_share'" not in at_line.split("AS ")[1]


def test_metric_inlining_fails_closed_when_referenced_metric_invalid():
    """Safeguard B: If referenced metric's SQL is null/invalid/dropped, referencing metric must drop."""
    id_sanitizer = IdentifierSanitizer()
    sanitizer = SemanticDDLSanitizer(id_sanitizer)
    drop_ledger = DropLedger()
    builder = MetricsClauseBuilder(
        id_sanitizer,
        _DummySchema(),
        sanitizer,
        translator=None,
        config=None,
        drop_ledger=drop_ledger,
    )

    # Reference metric that was nulled out (CAST(NULL AS DOUBLE))
    metrics_lines = [
        "  SALESFACT.\"PCT_CATEGORY_COMPETE_SHARE\" AS CAST(NULL AS DOUBLE)",
        "  SALESFACT.\"ATINDICATOR01\" AS CASE WHEN SALESFACT.\"PCT_CATEGORY_COMPETE_SHARE\" < 0.55 THEN 1 ELSE 2 END",
    ]
    metric_name_set = {"PCT_CATEGORY_COMPETE_SHARE", "ATINDICATOR01"}

    pruned = builder._prune_unresolved_metric_lines(metrics_lines, metric_name_set)

    # ATINDICATOR01 must be dropped (failed closed)
    at_lines = [l for l in pruned if "ATINDICATOR01" in l]
    assert len(at_lines) == 0


def test_metric_inlining_handles_nested_metric_references():
    """Safeguard C: Multi-level nested metric references (A -> B -> C) inline recursively."""
    id_sanitizer = IdentifierSanitizer()
    sanitizer = SemanticDDLSanitizer(id_sanitizer)
    drop_ledger = DropLedger()
    builder = MetricsClauseBuilder(
        id_sanitizer,
        _DummySchema(),
        sanitizer,
        translator=None,
        config=None,
        drop_ledger=drop_ledger,
    )

    metrics_lines = [
        "  SENTIMENT.\"SENTIMENT\" AS AVG(SENTIMENT.SCORE)",
        "  SENTIMENT.\"ATINDICATOR04\" AS CASE WHEN SENTIMENT.\"SENTIMENT\" < 65 THEN 1 ELSE 3 END",
        "  SENTIMENT.\"ATINDICATOR04A\" AS CASE WHEN SENTIMENT.\"ATINDICATOR04\" = 1 THEN 'Low' ELSE 'High' END",
    ]
    metric_name_set = {"SENTIMENT", "ATINDICATOR04", "ATINDICATOR04A"}

    pruned = builder._prune_unresolved_metric_lines(metrics_lines, metric_name_set)
    at04a_line = [l for l in pruned if "ATINDICATOR04A" in l][0]

    # ATINDICATOR04A must have recursively inlined both ATINDICATOR04 and SENTIMENT
    assert "AVG(SENTIMENT.SCORE)" in at04a_line
    assert "SENTIMENT.\"ATINDICATOR04\"" not in at04a_line


def test_metric_inlining_prevents_infinite_loop_on_table_column_same_as_metric_name():
    """Safeguard D: Prevent infinite loop when inlined expression contains physical column matching metric name."""
    id_sanitizer = IdentifierSanitizer()
    sanitizer = SemanticDDLSanitizer(id_sanitizer)
    drop_ledger = DropLedger()
    builder = MetricsClauseBuilder(
        id_sanitizer,
        _DummySchema(),
        sanitizer,
        translator=None,
        config=None,
        drop_ledger=drop_ledger,
    )

    metrics_lines = [
        '  SENTIMENT."SENTIMENT" AS AVG(SENTIMENT."SENTIMENT")',
        '  SENTIMENT."ATINDICATOR05" AS CASE WHEN SENTIMENT."SENTIMENT" < 65 THEN 1 ELSE 3 END',
        '  SENTIMENT."ATINDICATOR05A" AS CASE WHEN SENTIMENT."SENTIMENT" < 50 THEN 1 ELSE 2 END',
        '  SENTIMENT."SENTIMENT_GAP" AS SENTIMENT."SENTIMENT" - 70',
    ]
    metric_name_set = {"SENTIMENT", "ATINDICATOR05", "ATINDICATOR05A", "SENTIMENT_GAP"}

    pruned = builder._prune_unresolved_metric_lines(metrics_lines, metric_name_set)
    assert len(pruned) == 4
    at05_line = [l for l in pruned if "ATINDICATOR05" in l][0]
    assert 'AVG(SENTIMENT."SENTIMENT")' in at05_line

