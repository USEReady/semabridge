from semabridge.connectors.snowflake_emitter import SnowflakeEmitter


def test_runtime_hotfix_rewrites_comp_sf_suffixes_and_zero_metrics():
    sql = """
create or replace semantic view SEMABRIDGE.PUBLIC.COMPETITIVE_MARKETING_ANALYSIS_SEMANTIC
dimensions (
    MANUFACTURER.MANUFACTURER_802B as MANUFACTURER."MANUFACTURER" with synonyms=('Producer','Maker'),
    KPI.CATEGORY_791F as KPI."CATEGORY" with synonyms=('Type','Class','Grouping')
)
metrics (
    SALESFACT.TOTAL_VANARSDEL_UNITS as 0 with synonyms=('Total Van Arsdel Units'),
    SALESFACT.TOTAL_OTHER_UNITS as 0 with synonyms=('Sum Other Units'),
    SALESFACT.TOTAL_CATEGORY_VOLUME as 0 with synonyms=('Sum Category Volume'),
    SALESFACT.TOTAL_COMPETE_VOLUME as 0 with synonyms=('Sum Compete Volume'),
    SALESFACT.CATEGORY_COMPETE_SHARE as 0 with synonyms=('Category Compete Share'),
    SALESFACT.UNITS_MARKET_SHARE as 0 with synonyms=('Units Market Share'),
    SALESFACT.INDICATOR01 as 0 with synonyms=('Indicator01')
);
"""
    out = SnowflakeEmitter._apply_comp_sf_runtime_hotfix(sql)
    assert "MANUFACTURER_802B" not in out
    assert "CATEGORY_791F" not in out
    assert "SALESFACT.TOTAL_VANARSDEL_UNITS as 0" not in out
    assert "SALESFACT.TOTAL_OTHER_UNITS as 0" not in out
    assert "SALESFACT.TOTAL_CATEGORY_VOLUME as 0" not in out
    assert "SALESFACT.TOTAL_COMPETE_VOLUME as 0" not in out
    assert "SALESFACT.CATEGORY_COMPETE_SHARE as 0" not in out
    assert "SALESFACT.UNITS_MARKET_SHARE as 0" not in out
    assert "SALESFACT.INDICATOR01 as 0" not in out
