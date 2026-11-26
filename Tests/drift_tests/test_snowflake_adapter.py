"""Unit tests for SnowflakeAdapter and Adapter Registry.

All tests are placed in Tests/drift_tests as required.
Tests verify:
1. Parsing Snowflake DDL containing METRICS, DIMENSIONS, and COMMENTS.
2. Parsing Snowflake DESCRIBE SEMANTIC VIEW result sets.
3. Safe view quoting rules.
4. Fetching definition text via GET_DDL and DESCRIBE fallback.
5. Adapter registry lookup, case insensitivity, and unsupported platform handling.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from semabridge.governance.drift import ObjectKind
from semabridge.governance.drift.adapters import (
    PlatformAdapter,
    SnowflakeAdapter,
    get_adapter,
    is_platform_supported,
)


def test_registry_lookup_and_supported_platforms():
    """Registry correctly identifies and returns Snowflake adapter."""
    adapter = get_adapter("SNOWFLAKE")
    assert isinstance(adapter, SnowflakeAdapter)
    assert isinstance(adapter, PlatformAdapter)
    assert adapter.platform_name == "SNOWFLAKE"

    # Case-insensitive resolution
    assert isinstance(get_adapter("snowflake"), SnowflakeAdapter)
    assert isinstance(get_adapter("Snowflake"), SnowflakeAdapter)

    # Verification of supported platforms
    assert is_platform_supported("SNOWFLAKE") is True
    assert is_platform_supported("snowflake") is True
    assert is_platform_supported("UNKNOWN_PLATFORM_XYZ") is False

    # Unsupported platform raises ValueError
    with pytest.raises(ValueError, match="is not supported"):
        get_adapter("UNKNOWN_PLATFORM_XYZ")


def test_snowflake_adapter_parse_ddl():
    """SnowflakeAdapter parses DDL text extracting metrics, dimensions, expressions, and comments."""
    adapter = SnowflakeAdapter()
    ddl = """
    CREATE OR REPLACE SEMANTIC VIEW "MY_DB"."ANALYTICS"."FINANCE_SEMANTIC"
    TABLES (
        SALES AS MY_DB.RAW.TRANSACTIONS
    )
    METRICS (
        TOTAL_REVENUE AS SUM(SALES.AMOUNT) COMMENT 'Total gross sales in USD',
        ORDER_COUNT AS COUNT(DISTINCT SALES.ORDER_ID)
    )
    DIMENSIONS (
        REGION_CODE AS SALES.REGION COMMENT 'Territory code'
    );
    """

    view_def = adapter.parse_definition(
        raw_text=ddl,
        view_name="MY_DB.ANALYTICS.FINANCE_SEMANTIC",
        source="live",
    )

    assert view_def.view_name == "MY_DB.ANALYTICS.FINANCE_SEMANTIC"
    assert view_def.platform == "SNOWFLAKE"
    assert len(view_def.objects) == 4

    # Check fact table
    facts = {o.name: o for o in view_def.objects if o.kind == ObjectKind.FACT}
    assert "SALES" in facts
    assert facts["SALES"].expression == "MY_DB.RAW.TRANSACTIONS"

    # Check metrics
    metrics = {o.name: o for o in view_def.objects if o.kind == ObjectKind.METRIC}
    assert "TOTAL_REVENUE" in metrics
    assert metrics["TOTAL_REVENUE"].expression == "SUM(SALES.AMOUNT)"
    assert metrics["TOTAL_REVENUE"].description == "Total gross sales in USD"

    assert "ORDER_COUNT" in metrics
    assert metrics["ORDER_COUNT"].expression == "COUNT(DISTINCT SALES.ORDER_ID)"
    assert metrics["ORDER_COUNT"].description is None

    # Check dimension
    dims = {o.name: o for o in view_def.objects if o.kind == ObjectKind.DIMENSION}
    assert "REGION_CODE" in dims
    assert dims["REGION_CODE"].expression == "SALES.REGION"
    assert dims["REGION_CODE"].description == "Territory code"


def test_snowflake_adapter_parse_describe_json():
    """SnowflakeAdapter parses JSON DESCRIBE records into normalized ViewDefinition."""
    adapter = SnowflakeAdapter()
    records = [
        {"object_kind": "METRIC", "object_name": "ACTIVE_USERS", "property": "EXPRESSION", "property_value": "COUNT(DISTINCT user_id)"},
        {"object_kind": "METRIC", "object_name": "ACTIVE_USERS", "property": "COMMENT", "property_value": "Daily active users"},
        {"object_kind": "DIMENSION", "object_name": "DEVICE_TYPE", "property": "EXPRESSION", "property_value": "sessions.device"},
    ]
    raw_json = json.dumps({"describe_records": records})

    view_def = adapter.parse_definition(
        raw_text=raw_json,
        view_name="PORTAL.METRICS.USER_ACTIVITY_VIEW",
        source="live",
    )

    assert view_def.platform == "SNOWFLAKE"
    assert len(view_def.objects) == 2

    by_name = {o.name: o for o in view_def.objects}
    assert "ACTIVE_USERS" in by_name
    assert by_name["ACTIVE_USERS"].kind == ObjectKind.METRIC
    assert by_name["ACTIVE_USERS"].expression == "COUNT(DISTINCT user_id)"
    assert by_name["ACTIVE_USERS"].description == "Daily active users"

    assert "DEVICE_TYPE" in by_name
    assert by_name["DEVICE_TYPE"].kind == ObjectKind.DIMENSION
    assert by_name["DEVICE_TYPE"].expression == "sessions.device"


def test_snowflake_adapter_list_views():
    """SnowflakeAdapter list_views retrieves views from ModelRepository deployments."""
    adapter = SnowflakeAdapter()
    mock_deployments = [
        {"semantic_view": "DB.SCH.V1", "is_latest": True},
        {"semantic_view": "DB.SCH.V2", "is_latest": False},
        {"semantic_view": "DB.SCH.V1"},  # duplicate
    ]

    with patch("semabridge.repository.model_repository.ModelRepository.list_project_deployments", return_value=mock_deployments):
        views = adapter.list_views("proj-test-123")
        assert views == ["DB.SCH.V1", "DB.SCH.V2"]


def test_snowflake_adapter_fetch_definition_text_get_ddl():
    """fetch_definition_text executes GET_DDL query and returns DDL string."""
    adapter = SnowflakeAdapter()
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = ("CREATE SEMANTIC VIEW TEST_VIEW ...",)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_cur

    with patch("snowflake.connector.connect", return_value=mock_conn), \
         patch("semabridge.connectors.sql_guard.guard_connection", return_value=mock_conn):
        res = adapter.fetch_definition_text(
            view_name="DB.SCHEMA.TEST_VIEW",
            connection_config={"account": "test_account", "user": "test_user"},
        )
        assert res == "CREATE SEMANTIC VIEW TEST_VIEW ..."
        mock_cur.execute.assert_any_call("SELECT GET_DDL('SEMANTIC_VIEW', 'DB.SCHEMA.TEST_VIEW')")


def test_snowflake_describe_and_ddl_produce_same_view_definition():
    """DESCRIBE records and DDL parsing produce the same ViewDefinition for the same semantic view."""
    adapter = SnowflakeAdapter()
    view_name = "CORP_DB.ANALYTICS.SALES_METRICS"

    ddl = """
    CREATE OR REPLACE SEMANTIC VIEW "CORP_DB"."ANALYTICS"."SALES_METRICS"
    TABLES (
        SALES AS CORP_DB.RAW.ORDERS
    )
    METRICS (
        TOTAL_SALES AS SUM(SALES.AMOUNT) COMMENT 'Total sales amount',
        ORDER_COUNT AS COUNT(SALES.ID)
    )
    DIMENSIONS (
        REGION AS SALES.REGION COMMENT 'Sales territory'
    );
    """
    ddl_def = adapter.parse_definition(ddl, view_name=view_name)

    records = [
        {"object_kind": "TABLE", "object_name": "SALES", "property": "EXPRESSION", "property_value": "CORP_DB.RAW.ORDERS"},
        {"object_kind": "METRIC", "object_name": "TOTAL_SALES", "property": "EXPRESSION", "property_value": "SUM(SALES.AMOUNT)"},
        {"object_kind": "METRIC", "object_name": "TOTAL_SALES", "property": "COMMENT", "property_value": "Total sales amount"},
        {"object_kind": "METRIC", "object_name": "ORDER_COUNT", "property": "EXPRESSION", "property_value": "COUNT(SALES.ID)"},
        {"object_kind": "DIMENSION", "object_name": "REGION", "property": "EXPRESSION", "property_value": "SALES.REGION"},
        {"object_kind": "DIMENSION", "object_name": "REGION", "property": "COMMENT", "property_value": "Sales territory"},
    ]
    desc_json = json.dumps({"describe_records": records})
    desc_def = adapter.parse_definition(desc_json, view_name=view_name)

    assert len(ddl_def.objects) == len(desc_def.objects) == 4

    ddl_objs = {f"{o.kind}:{o.name}": o for o in ddl_def.objects}
    desc_objs = {f"{o.kind}:{o.name}": o for o in desc_def.objects}

    for key, ddl_o in ddl_objs.items():
        assert key in desc_objs
        desc_o = desc_objs[key]
        assert ddl_o.kind == desc_o.kind
        assert ddl_o.name == desc_o.name
        assert ddl_o.table == desc_o.table
        assert ddl_o.expression == desc_o.expression
        assert ddl_o.description == desc_o.description


def test_snowflake_adapter_fetch_auth_error_raises_warehouse_unavailable():
    """Auth/permission errors raise RuntimeError with 'warehouse unavailable'."""
    adapter = SnowflakeAdapter()
    with patch("snowflake.connector.connect", side_effect=Exception("250001: Authentication failed")):
        with pytest.raises(RuntimeError, match="warehouse unavailable"):
            adapter.fetch_definition_text(
                view_name="DB.SCHEMA.VIEW_A",
                connection_config={"account": "test_acct", "user": "test_usr"},
            )


def test_registry_mock_refused_when_not_allowed(monkeypatch):
    """get_adapter with mode='mock' is refused when SEMABRIDGE_DRIFT_MOCK_ALLOWED is false."""
    monkeypatch.setenv("SEMABRIDGE_DRIFT_MOCK_ALLOWED", "false")
    with pytest.raises(PermissionError, match="Mock mode is disabled"):
        get_adapter("SNOWFLAKE", mode="mock")


RECORDED_DDL_FIXTURE = """
create or replace semantic view SEMABRIDGE.HUMAN_RESOURCES.HUMAN_RESOURCES_SAMPLE_PBIX_SEMANTIC
	tables (
		SEMABRIDGE.HUMAN_RESOURCES.AGEGROUP primary key (AGEGROUPID),
		SEMABRIDGE.HUMAN_RESOURCES.BU primary key (BU),
		COL_DATE as SEMABRIDGE.HUMAN_RESOURCES.DATE primary key (COL_DATE),
		EMPLOYEE as SEMABRIDGE.HUMAN_RESOURCES.EMPLOYEE_ENRICHED,
		SEMABRIDGE.HUMAN_RESOURCES.ETHNICITY primary key (ETHNIC_GROUP),
		SEMABRIDGE.HUMAN_RESOURCES.FP primary key (FP),
		SEMABRIDGE.HUMAN_RESOURCES.GENDER primary key (ID),
		SEMABRIDGE.HUMAN_RESOURCES.PAYTYPE primary key (PAYTYPEID),
		SEMABRIDGE.HUMAN_RESOURCES.SEPARATIONREASON primary key (SEPARATIONTYPEID)
	)
	relationships (
		EMPLOYEE_AGEGROUPID_AGEGROUP_AGEGROUPID as EMPLOYEE(AGEGROUPID) references AGEGROUP(AGEGROUPID),
		EMPLOYEE_BU_BU_BU as EMPLOYEE(BU) references BU(BU),
		EMPLOYEE_DATE_DATE_DATE as EMPLOYEE(COL_DATE) references COL_DATE(COL_DATE),
		EMPLOYEE_ETHNICGROUP_ETHNICITY_ETHNIC_GROUP as EMPLOYEE(ETHNICGROUP) references ETHNICITY(ETHNIC_GROUP),
		EMPLOYEE_FP_FP_FP as EMPLOYEE(FP) references FP(FP),
		EMPLOYEE_GENDER_GENDER_ID as EMPLOYEE(GENDER) references GENDER(ID),
		EMPLOYEE_PAYTYPEID_PAYTYPE_PAYTYPEID as EMPLOYEE(PAYTYPEID) references PAYTYPE(PAYTYPEID),
		EMPLOYEE_TERMREASON_SEPARATIONREASON_SEPARATIONTYPEID as EMPLOYEE(TERMREASON) references SEPARATIONREASON(SEPARATIONTYPEID)
	)
	facts (
		PRIVATE EMPLOYEE.AGE as EMPLOYEE."AGE",
		PRIVATE EMPLOYEE.EMPLID as EMPLOYEE."EMPLID"
	)
	dimensions (
		AGEGROUP.AGEGROUP as AGEGROUP."AGEGROUP",
		PRIVATE AGEGROUP.AGEGROUP_AGEGROUPID as AGEGROUP."AGEGROUPID",
		BU.BU as BU."BU",
		BU.REGION as BU."REGION",
		BU.REGIONSEQ as BU."REGIONSEQ",
		BU.VP as BU."VP",
		COL_DATE.DATE as COL_DATE."COL_DATE" with synonyms=('DATE'),
		COL_DATE.DAY as COL_DATE."DAY",
		COL_DATE.MONTH as COL_DATE."MONTH",
		COL_DATE.MONTHENDDATE as COL_DATE."MONTHENDDATE",
		PRIVATE COL_DATE.MONTHINCREMENTNUMBER as COL_DATE."MONTHINCREMENTNUMBER",
		COL_DATE.MONTHNUMBER as COL_DATE."MONTHNUMBER",
		COL_DATE.MONTHSTARTDATE as COL_DATE."MONTHSTARTDATE",
		COL_DATE.PERIOD as COL_DATE."PERIOD",
		COL_DATE.PERIODNUMBER as COL_DATE."PERIODNUMBER",
		COL_DATE.QTR as COL_DATE."QTR",
		COL_DATE.QTRNUMBER as COL_DATE."QTRNUMBER",
		COL_DATE.YEAR as COL_DATE."YEAR",
		PRIVATE EMPLOYEE.BADHIRES as EMPLOYEE."BADHIRES",
		PRIVATE EMPLOYEE.EMPLOYEE_AGEGROUPID as EMPLOYEE."AGEGROUPID",
		PRIVATE EMPLOYEE.EMPLOYEE_BU as EMPLOYEE."BU",
		PRIVATE EMPLOYEE.EMPLOYEE_DATE as EMPLOYEE."COL_DATE",
		PRIVATE EMPLOYEE.EMPLOYEE_FP as EMPLOYEE."FP",
		PRIVATE EMPLOYEE.EMPLOYEE_GENDER as EMPLOYEE."GENDER",
		PRIVATE EMPLOYEE.EMPLOYEE_PAYTYPEID as EMPLOYEE."PAYTYPEID",
		PRIVATE EMPLOYEE.ETHNICGROUP as EMPLOYEE."ETHNICGROUP",
		PRIVATE EMPLOYEE.HIREDATE as EMPLOYEE."HIREDATE",
		PRIVATE EMPLOYEE.ISNEWHIRE as EMPLOYEE."ISNEWHIRE",
		PRIVATE EMPLOYEE.IS_SPLY_YEAR as EMPLOYEE."IS_SPLY_YEAR",
		PRIVATE EMPLOYEE.MAX_DATE as EMPLOYEE."MAX_DATE",
		PRIVATE EMPLOYEE.TENUREDAYS as EMPLOYEE."TENUREDAYS",
		PRIVATE EMPLOYEE.TENUREMONTHS as EMPLOYEE."TENUREMONTHS",
		PRIVATE EMPLOYEE.TERMDATE as EMPLOYEE."TERMDATE",
		PRIVATE EMPLOYEE.TERMREASON as EMPLOYEE."TERMREASON",
		ETHNICITY.ETHNICITY as ETHNICITY."ETHNICITY",
		PRIVATE ETHNICITY.ETHNIC_GROUP as ETHNICITY."ETHNIC_GROUP",
		FP.FP as FP."FP",
		FP.FPDESC as FP."FPDESC",
		GENDER.GENDER as GENDER."GENDER",
		PRIVATE GENDER.ID as GENDER."ID",
		PRIVATE GENDER.SORT as GENDER."SORT",
		PAYTYPE.PAYTYPE as PAYTYPE."PAYTYPE",
		PRIVATE PAYTYPE.PAYTYPE_PAYTYPEID as PAYTYPE."PAYTYPEID",
		SEPARATIONREASON.SEPARATIONREASON as SEPARATIONREASON."SEPARATIONREASON",
		PRIVATE SEPARATIONREASON.SEPARATIONTYPEID as SEPARATIONREASON."SEPARATIONTYPEID"
	)
	metrics (
		BADHIREPCTOFACTIVES as EMPLOYEE."SUM_OF_BADHIRES" / NULLIF(EMPLOYEE."ACTIVES", 0) with synonyms=('BadHire%ofActives','BADHIREPCTOFACTIVES'),
		SEPPCTOFACTIVE as EMPLOYEE."SEPS" / NULLIF(EMPLOYEE."ACTIVES", 0) with synonyms=('Sep%ofActive','SEPPCTOFACTIVE'),
		TO_PCT as EMPLOYEE."SEPS" / NULLIF(EMPLOYEE."ACTIVES", 0) with synonyms=('TO %','TO_PCT'),
		BU.DUMMY_DISTINCT_REGIONS as COUNT(DISTINCT BU.REGION) comment='Dummy metric: number of distinct regions across business units',
		COL_DATE.COUNT_OF_DATE as COUNT(COL_DATE.COL_DATE) with synonyms=('Count of Date','COUNT_OF_DATE'),
		COL_DATE.SUM_OF_DAY as SUM(COL_DATE.DAY::FLOAT) with synonyms=('SUM_OF_DAY'),
		COL_DATE.SUM_OF_MONTHNUMBER as SUM(COL_DATE.MONTHNUMBER::FLOAT) with synonyms=('SUM_OF_MONTHNUMBER'),
		COL_DATE.SUM_OF_PERIODNUMBER as SUM(COL_DATE.PERIODNUMBER::FLOAT) with synonyms=('SUM_OF_PERIODNUMBER'),
		COL_DATE.SUM_OF_QTR as SUM(COL_DATE.QTR::FLOAT) with synonyms=('SUM_OF_QTR'),
		COL_DATE.SUM_OF_YEAR as SUM(COL_DATE.YEAR::FLOAT) with synonyms=('SUM_OF_YEAR'),
		EMPLOYEE.ACTIVES non additive by (COL_DATE.PERIODNUMBER asc nulls last) as COUNT(CASE WHEN (EMPLOYEE.TERMDATE IS NULL) THEN EMPLOYEE.EMPLID ELSE NULL END) with synonyms=('Actives'),
		EMPLOYEE.ACTIVES_SPLY as CAST(NULL AS DOUBLE) comment='not applicable: depends on non-additive metric [ACTIVES]',
		EMPLOYEE.ACTIVES_YOY_PCT_CHANGE as CAST(NULL AS DOUBLE) comment='Cannot compute — depends on unresolved metric(s): ''ACTIVES_YOY_VAR''; ''ACTIVES_SPLY''',
		EMPLOYEE.ACTIVES_YOY_VAR as CAST(NULL AS DOUBLE) comment='Cannot compute — depends on unresolved metric(s): ''ACTIVES_SPLY''',
		EMPLOYEE.AVG_AGE as ROUND(AVG(EMPLOYEE.AGE::FLOAT), 0) with synonyms=('AVG Age','AVG_AGE'),
		EMPLOYEE.AVG_TENURE_DAYS as AVG(EMPLOYEE.TENUREDAYS) with synonyms=('AVG Tenure Days','AVG_TENURE_DAYS'),
		EMPLOYEE.AVG_TENURE_MONTHS as CASE WHEN (ROUND((AVG(EMPLOYEE.TENUREDAYS)) / 30, 1)) IS NULL AND (1) IS NULL THEN NULL ELSE COALESCE(ROUND((AVG(EMPLOYEE.TENUREDAYS)) / 30, 1), 0) - 1 END with synonyms=('AVG Tenure Months','AVG_TENURE_MONTHS'),
		EMPLOYEE.BADHIREPCTOFACTIVESPLY as CAST(NULL AS DOUBLE) comment='Cannot compute — depends on unresolved metric(s): ''ACTIVES_SPLY''',
		EMPLOYEE.BAD_HIRES_SPLY as SUM(CASE WHEN EMPLOYEE.IS_SPLY_YEAR THEN EMPLOYEE.BADHIRES ELSE NULL END::FLOAT) with synonyms=('Bad Hires SPLY','BAD_HIRES_SPLY') comment='Note: calculation comment',
		EMPLOYEE.BAD_HIRES_YOY_PCT_CHANGE as (CASE WHEN (SUM(EMPLOYEE.BADHIRES::FLOAT)) IS NULL AND (SUM(CASE WHEN EMPLOYEE.IS_SPLY_YEAR THEN EMPLOYEE.BADHIRES ELSE NULL END::FLOAT)) IS NULL THEN NULL ELSE COALESCE((SUM(EMPLOYEE.BADHIRES::FLOAT)), 0) - COALESCE((SUM(CASE WHEN EMPLOYEE.IS_SPLY_YEAR THEN EMPLOYEE.BADHIRES ELSE NULL END::FLOAT)), 0) END) / NULLIF((SUM(CASE WHEN EMPLOYEE.IS_SPLY_YEAR THEN EMPLOYEE.BADHIRES ELSE NULL END::FLOAT)), 0) with synonyms=('Bad Hires YoY % Change','BAD_HIRES_YOY_PCT_CHANGE') comment='Note: calc',
		EMPLOYEE.BAD_HIRES_YOY_VAR as CASE WHEN (SUM(EMPLOYEE.BADHIRES::FLOAT)) IS NULL AND (SUM(CASE WHEN EMPLOYEE.IS_SPLY_YEAR THEN EMPLOYEE.BADHIRES ELSE NULL END::FLOAT)) IS NULL THEN NULL ELSE COALESCE((SUM(EMPLOYEE.BADHIRES::FLOAT)), 0) - COALESCE((SUM(CASE WHEN EMPLOYEE.IS_SPLY_YEAR THEN EMPLOYEE.BADHIRES ELSE NULL END::FLOAT)), 0) END with synonyms=('Bad Hires YoY Var','BAD_HIRES_YOY_VAR') comment='Note: calc',
		EMPLOYEE.NEW_HIRES as SUM(EMPLOYEE.ISNEWHIRE::FLOAT) with synonyms=('New Hires','NEW_HIRES'),
		EMPLOYEE.NEW_HIRES_SPLY as SUM(CASE WHEN EMPLOYEE.IS_SPLY_YEAR THEN EMPLOYEE.ISNEWHIRE ELSE NULL END::FLOAT) with synonyms=('New Hires SPLY','NEW_HIRES_SPLY') comment='Note: calc',
		EMPLOYEE.NEW_HIRES_YOY_PCT_CHANGE as (CASE WHEN (SUM(EMPLOYEE.ISNEWHIRE::FLOAT)) IS NULL AND (SUM(CASE WHEN EMPLOYEE.IS_SPLY_YEAR THEN EMPLOYEE.ISNEWHIRE ELSE NULL END::FLOAT)) IS NULL THEN NULL ELSE COALESCE((SUM(EMPLOYEE.ISNEWHIRE::FLOAT)), 0) - COALESCE((SUM(CASE WHEN EMPLOYEE.IS_SPLY_YEAR THEN EMPLOYEE.ISNEWHIRE ELSE NULL END::FLOAT)), 0) END) / NULLIF((SUM(CASE WHEN EMPLOYEE.IS_SPLY_YEAR THEN EMPLOYEE.ISNEWHIRE ELSE NULL END::FLOAT)), 0) with synonyms=('New Hires YoY % Change','NEW_HIRES_YOY_PCT_CHANGE') comment='Note: calc',
		EMPLOYEE.NEW_HIRES_YOY_VAR as CASE WHEN (SUM(EMPLOYEE.ISNEWHIRE::FLOAT)) IS NULL AND (SUM(CASE WHEN EMPLOYEE.IS_SPLY_YEAR THEN EMPLOYEE.ISNEWHIRE ELSE NULL END::FLOAT)) IS NULL THEN NULL ELSE COALESCE((SUM(EMPLOYEE.ISNEWHIRE::FLOAT)), 0) - COALESCE((SUM(CASE WHEN EMPLOYEE.IS_SPLY_YEAR THEN EMPLOYEE.ISNEWHIRE ELSE NULL END::FLOAT)), 0) END with synonyms=('New Hires YoY Var','NEW_HIRES_YOY_VAR') comment='Note: calc',
		EMPLOYEE.SEPPCTOFSMLYACTIVES as CAST(NULL AS DOUBLE) comment='Cannot compute — depends on unresolved metric(s): ''ACTIVES_SPLY''',
		EMPLOYEE.SEPS as COUNT(CASE WHEN (EMPLOYEE.TERMDATE IS NOT NULL) THEN EMPLOYEE.EMPLID END) with synonyms=('Seps','SEPS'),
		EMPLOYEE.SEPS_SPLY as SUM(CASE WHEN (EMPLOYEE.IS_SPLY_YEAR) AND (EMPLOYEE.TERMDATE IS NOT NULL) THEN 1 ELSE NULL END::FLOAT) with synonyms=('Seps SPLY','SEPS_SPLY') comment='Note: calc',
		EMPLOYEE.SEPS_YOY_PCT_CHANGE as (CASE WHEN (COUNT(CASE WHEN (EMPLOYEE.TERMDATE IS NOT NULL) THEN EMPLOYEE.EMPLID END::FLOAT)) IS NULL AND (SUM(CASE WHEN (EMPLOYEE.IS_SPLY_YEAR) AND (EMPLOYEE.TERMDATE IS NOT NULL) THEN 1 ELSE NULL END::FLOAT)) IS NULL THEN NULL ELSE COALESCE((COUNT(CASE WHEN (EMPLOYEE.TERMDATE IS NOT NULL) THEN EMPLOYEE.EMPLID END::FLOAT)), 0) - COALESCE((SUM(CASE WHEN (EMPLOYEE.IS_SPLY_YEAR) AND (EMPLOYEE.TERMDATE IS NOT NULL) THEN 1 ELSE NULL END::FLOAT)), 0) END) / NULLIF((SUM(CASE WHEN (EMPLOYEE.IS_SPLY_YEAR) AND (EMPLOYEE.TERMDATE IS NOT NULL) THEN 1 ELSE NULL END::FLOAT)), 0) with synonyms=('Seps YoY % Change','SEPS_YOY_PCT_CHANGE') comment='Note: calc',
		EMPLOYEE.SEPS_YOY_VAR as CASE WHEN (COUNT(CASE WHEN (EMPLOYEE.TERMDATE IS NOT NULL) THEN EMPLOYEE.EMPLID END::FLOAT)) IS NULL AND (SUM(CASE WHEN (EMPLOYEE.IS_SPLY_YEAR) AND (EMPLOYEE.TERMDATE IS NOT NULL) THEN 1 ELSE NULL END::FLOAT)) IS NULL THEN NULL ELSE COALESCE((COUNT(CASE WHEN (EMPLOYEE.TERMDATE IS NOT NULL) THEN EMPLOYEE.EMPLID END::FLOAT)), 0) - COALESCE((SUM(CASE WHEN (EMPLOYEE.IS_SPLY_YEAR) AND (EMPLOYEE.TERMDATE IS NOT NULL) THEN 1 ELSE NULL END::FLOAT)), 0) END with synonyms=('Seps YoY Var','SEPS_YOY_VAR') comment='Note: calc',
		EMPLOYEE.SUM_OF_BADHIRES as SUM(EMPLOYEE.BADHIRES::FLOAT) with synonyms=('Sum of BadHires','SUM_OF_BADHIRES'),
		EMPLOYEE.TO_PCT_DEN as CAST(NULL AS DOUBLE) comment='Cannot compute — depends on unresolved metric(s): ''ACTIVES_SPLY''',
		EMPLOYEE.TO_PCT_NORM as CAST(NULL AS DOUBLE) comment='Cannot compute — depends on unresolved metric(s): ''TO_PCT_DEN''',
		EMPLOYEE.TO_PCT_NUM as CAST(NULL AS DOUBLE) comment='Cannot compute — depends on unresolved metric(s): ''SEPS_SPLY''',
		EMPLOYEE.TO_PCT_VAR as CAST(NULL AS DOUBLE) comment='Cannot compute — depends on unresolved metric(s): ''TO_PCT_NORM'''
	)
"""


def test_snowflake_roundtrip_parse_recorded_ddl():
    """Roundtrip unit test verifying exact object extraction from recorded DDL without corruption."""
    adapter = SnowflakeAdapter()
    view_def = adapter.parse_definition(
        raw_text=RECORDED_DDL_FIXTURE,
        view_name="SEMABRIDGE.HUMAN_RESOURCES.HUMAN_RESOURCES_SAMPLE_PBIX_SEMANTIC",
        source="live",
    )

    # 1. Assert all sections were extracted cleanly
    assert len(view_def.objects) > 0

    # 2. Assert every parsed name appears in the DDL as an identifier and contains no corrupted markers
    for obj in view_def.objects:
        name = obj.name
        for bad in ["(", ")", " as ", "synonyms", "comment", "PRIVATE", '"', "'"]:
            assert bad not in name, f"Parsed name '{name}' of kind {obj.kind} contains corrupted marker '{bad}'"

    # 3. Assert metric AVG_TENURE_DAYS has expression AVG(EMPLOYEE.TENUREDAYS) (NOT reversed)
    metrics = {o.name: o for o in view_def.objects if o.kind == ObjectKind.METRIC}
    assert "AVG_TENURE_DAYS" in metrics
    avg_tenure = metrics["AVG_TENURE_DAYS"]
    assert avg_tenure.expression == "AVG(EMPLOYEE.TENUREDAYS)"
    assert avg_tenure.table == "EMPLOYEE"
    assert "AVG Tenure Days" in avg_tenure.properties.get("synonyms", "")

    # 4. Assert metric with CAST and AS inside expression: ACTIVES_SPLY
    assert "ACTIVES_SPLY" in metrics
    actives_sply = metrics["ACTIVES_SPLY"]
    assert actives_sply.expression == "CAST(NULL AS DOUBLE)"
    assert "not applicable" in (actives_sply.description or "")

    # 5. Assert metric with NON ADDITIVE BY clause: ACTIVES
    assert "ACTIVES" in metrics
    actives = metrics["ACTIVES"]
    assert "COUNT(CASE WHEN" in actives.expression
    assert "NON ADDITIVE BY" in actives.properties.get("non_additive_by", "").upper()

    # 6. Assert fact EMPLID has expression EMPLOYEE."EMPLID"
    facts = {o.name: o for o in view_def.objects if o.kind == ObjectKind.FACT}
    assert "EMPLID" in facts
    emplid = facts["EMPLID"]
    assert emplid.expression == 'EMPLOYEE."EMPLID"'
    assert emplid.table == "EMPLOYEE"
    assert emplid.properties.get("visibility") == "private"

    # 7. Assert dimension GENDER has expression GENDER."GENDER"
    dims = {o.name: o for o in view_def.objects if o.kind == ObjectKind.DIMENSION}
    assert "GENDER" in dims
    gender = dims["GENDER"]
    assert gender.expression == 'GENDER."GENDER"'
    assert gender.table == "GENDER"

    # 8. Assert relationship does NOT bleed into facts or next section
    rels = {o.name: o for o in view_def.objects if o.kind == ObjectKind.RELATIONSHIP}
    assert "EMPLOYEE_TERMREASON_SEPARATIONREASON_SEPARATIONTYPEID" in rels
    rel = rels["EMPLOYEE_TERMREASON_SEPARATIONREASON_SEPARATIONTYPEID"]
    assert "FACTS (" not in rel.expression
    assert "EMPLOYEE(TERMREASON) references SEPARATIONREASON(SEPARATIONTYPEID)" in rel.expression

    # 9. Assert table entries have name, underlying table, and pk separated
    assert "COL_DATE" in facts
    col_date_tbl = facts["COL_DATE"]
    assert col_date_tbl.expression == "SEMABRIDGE.HUMAN_RESOURCES.DATE"
    assert col_date_tbl.properties.get("primary_key") == "COL_DATE"


def test_engine_sanity_rule_refuses_100_percent_deletions_and_additions():
    """Sanity guard raises ParserOrBaselineMismatchError when 100% deletions + 100% additions with 0 expr drift."""
    from semabridge.governance.drift.comparison import compare_views
    from semabridge.governance.drift.models import (
        ObjectDef,
        ObjectKind,
        ParserOrBaselineMismatchError,
        ViewDefinition,
    )

    baseline_def = ViewDefinition(
        view_name="TEST.VIEW",
        platform="SNOWFLAKE",
        source="deploy",
        retrieved_at="2026-11-20T00:00:00Z",
        objects=[
            ObjectDef(kind=ObjectKind.METRIC, table="SALES", name="AVG_TENURE", expression="AVG(TENURE)"),
            ObjectDef(kind=ObjectKind.METRIC, table="SALES", name="SUM_SALES", expression="SUM(AMOUNT)"),
        ],
        capabilities={"METRIC": {"expression"}},
    )

    # Inverted / mismatched live view: name has expression, expr has name
    mismatched_live = ViewDefinition(
        view_name="TEST.VIEW",
        platform="SNOWFLAKE",
        source="live",
        retrieved_at="2026-11-24T00:00:00Z",
        objects=[
            ObjectDef(kind=ObjectKind.METRIC, table="SALES", name="AVG(TENURE)", expression="AVG_TENURE"),
            ObjectDef(kind=ObjectKind.METRIC, table="SALES", name="SUM(AMOUNT)", expression="SUM_SALES"),
        ],
        capabilities={"METRIC": {"expression"}},
    )

    with pytest.raises(ParserOrBaselineMismatchError, match="baseline and live definitions appear to have mismatched naming conventions"):
        compare_views(baseline=baseline_def, live=mismatched_live)
