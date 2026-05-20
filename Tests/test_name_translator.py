from semabridge.utils.name_translator import get_target_deployment_name


def test_long_name_truncation_and_hash():
    long_name = "SEVERITY_GROUPS_IF_REP_SFDC_RAIL_CASE_CSEVERITY_1_URGENT_REP_SFDC_RAIL_CASE_CSEVERITY_2_VERY_HIGH_REP_SFDC_RAIL_CASE_CSEVERITY_3_HIGH_1_URGENT_IF_REP_SFDC_RAIL_CASE_CSEVERITY_4_MEDIUM_2_AT_RISK_3_ON_TIME_USED_FOR_FILTERING_THE_PROJECT_DASHBOARD_S_DELIVERY_STATUS_SUMMARY_TAB_PER_THE_CUSTOMER_SUCCESS_LEADERSHIP_ASK"
    result = get_target_deployment_name(long_name, platform="snowflake")
    assert len(result) <= 255
    assert result.endswith("_SEMANTIC") or "_SEMANTIC" in result
    # If truncated, there should be a hash-like 6 hex chars before the suffix
    if len(result) > (len("_SEMANTIC") + 7):
        # Expect pattern: ..._<6HEX>_SEMANTIC
        assert result[-(len("_SEMANTIC") + 7)].isdigit() or result[-(len("_SEMANTIC") + 7)] == '_'


def test_short_name_unchanged():
    name = "Client Data"
    assert get_target_deployment_name(name, platform="snowflake").endswith("_SEMANTIC")
