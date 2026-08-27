import pytest
from semabridge.connectors.semantic_ddl_sanitizer import SemanticDDLSanitizer
from semabridge.utils.identifiers import IdentifierSanitizer

def test_targeted_metric_remediation_preserves_unrelated_metrics():
    """
    Regression Test:
    Verify that when Snowflake reports an invalid identifier for a specific broken metric (e.g. COL_DATE.RUNNING_YEAR in KPI.KPI01),
    SemanticDDLSanitizer ONLY nulls out KPI.KPI01 and preserves all other metrics referencing COL_DATE (e.g. TOTAL_UNITS_YTD, TOTAL_UNITS_SPLY).
    """
    synthetic_ddl = """CREATE SEMANTIC VIEW MOCK_DB.MOCK_SCHEMA.MOCK_SEMANTIC
  TABLES (
    COL_DATE as MOCK_DB.MOCK_SCHEMA.DATE primary key (COL_DATE),
    KPI as MOCK_DB.MOCK_SCHEMA.KPI_ENRICHED primary key (CATEGORY),
    SALESFACT as MOCK_DB.MOCK_SCHEMA.SALESFACT_ENRICHED primary key (PRODUCTID)
  )
  RELATIONSHIPS (
    SALESFACT_DATE_DATE_DATE as SALESFACT(COL_DATE) references COL_DATE(COL_DATE)
  )
  DIMENSIONS (
    COL_DATE.COL_DATE as COL_DATE."COL_DATE",
    COL_DATE.RUNNING_YEAR as COL_DATE."RUNNING_YEAR",
    SALESFACT.PRODUCTID as SALESFACT."PRODUCTID",
    SALESFACT.COL_DATE as SALESFACT."COL_DATE",
    SALESFACT.IS_YTD as SALESFACT."IS_YTD",
    SALESFACT.IS_SPLY_YEAR as SALESFACT."IS_SPLY_YEAR"
  )
  METRICS (
    KPI."KPI01" AS CASE WHEN SUM(KPI.KPI::FLOAT) = 1 THEN SUM(CASE WHEN COL_DATE.RUNNING_YEAR = 1 THEN SALESFACT.UNITS ELSE NULL END::FLOAT) ELSE NULL END,
    SALESFACT."TOTAL_UNITS_YTD" AS SUM(CASE WHEN SALESFACT.IS_YTD THEN SALESFACT.UNITS ELSE NULL END::FLOAT),
    SALESFACT."TOTAL_UNITS_SPLY" AS SUM(CASE WHEN SALESFACT.IS_SPLY_YEAR THEN SALESFACT.UNITS ELSE NULL END::FLOAT),
    SALESFACT."PCT_UNITS_MARKET_SHARE_YTD" AS COALESCE((SUM(SALESFACT.UNITS::FLOAT)) / NULLIF(SUM(CASE WHEN SALESFACT.IS_YTD THEN SALESFACT.UNITS ELSE NULL END::FLOAT), 0), 0)
  );"""

    id_sanitizer = IdentifierSanitizer()
    sanitizer = SemanticDDLSanitizer(identifier_sanitizer=id_sanitizer)
    invalid_identifier = "COL_DATE.RUNNING_YEAR"

    remediated_ddl, changed, nulled_metrics = sanitizer.remediate_invalid_identifier(
        synthetic_ddl, invalid_identifier
    )

    assert changed is True, "Sanitizer should report changes made"
    assert nulled_metrics == ["KPI01"], f"Expected ONLY 'KPI01' to be nulled, but got: {nulled_metrics}"

    # Verify KPI01 is nulled out
    assert 'KPI."KPI01" AS CAST(NULL AS DOUBLE),' in remediated_ddl

    # Verify all 3 working metrics referencing COL_DATE/SALESFACT remain intact and UNTOUCHED!
    assert 'SALESFACT."TOTAL_UNITS_YTD" AS SUM(CASE WHEN SALESFACT.IS_YTD THEN SALESFACT.UNITS ELSE NULL END::FLOAT),' in remediated_ddl
    assert 'SALESFACT."TOTAL_UNITS_SPLY" AS SUM(CASE WHEN SALESFACT.IS_SPLY_YEAR THEN SALESFACT.UNITS ELSE NULL END::FLOAT),' in remediated_ddl
    assert 'SALESFACT."PCT_UNITS_MARKET_SHARE_YTD" AS COALESCE((SUM(SALESFACT.UNITS::FLOAT)) / NULLIF(SUM(CASE WHEN SALESFACT.IS_YTD THEN SALESFACT.UNITS ELSE NULL END::FLOAT), 0), 0)' in remediated_ddl
