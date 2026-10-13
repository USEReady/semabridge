import pytest
from semabridge.connectors.table_binding_resolver import (
    compute_column_compatibility,
    rank_table_candidates,
    resolve_dataset_to_physical_table,
    check_table_name_collision_blocker,
)
from semabridge.connectors.sql_guard import (
    validate_safe_sql,
    set_allowed_absent_tables,
    clear_allowed_absent_tables,
    SQLSecurityGuardError,
)

def test_binding_requires_high_column_compatibility():
    # Model table 'Sales' has columns that do not match live 'SALES_FACT' (compat < 0.8)
    model_cols = ["Opportunity ID", "Sales Stage ID", "Product ID", "ProductRevenue", "Sales Date", "Closed Date", "Account ID", "Partner ID"]
    live_sales_fact = {"UNITS", "DATE", "PRODUCT_ID", "ZIP", "REVENUE"} # low overlap (< 80%)

    live_meta = {
        "SALES_FACT": live_sales_fact,
        "DATE": {"DATE", "YEAR", "MONTH"},
        "PRODUCT": {"PRODUCT_ID", "PRODUCT", "CATEGORY"},
    }

    # Revenue Opportunities' Sales must NOT bind to SALES_FACT
    res = resolve_dataset_to_physical_table("Sales", model_cols, live_meta)
    assert res == "Sales", f"Expected 'Sales' (unbound), got {res}"

    # Dates must NOT bind to DATE
    date_cols = ["Date", "Year", "Day", "Month", "Day Of Week Name", "Week of Year", "Month (Sort Order)", "Day (Sort Order)"]
    res_date = resolve_dataset_to_physical_table("Dates", date_cols, live_meta)
    assert res_date == "Dates", f"Expected 'Dates' (unbound), got {res_date}"

    # Products must NOT bind to PRODUCT
    prod_cols = ["Product Code", "Product ID"]
    res_prod = resolve_dataset_to_physical_table("Products", prod_cols, live_meta)
    assert res_prod == "Products", f"Expected 'Products' (unbound), got {res_prod}"


def test_binding_succeeds_when_high_compatibility():
    # Corporate Spend 'Date' has 100% column overlap with SPEND_DATE
    spend_date_cols = ["Date"]
    live_meta = {
        "SPEND_DATE": {"DATE"},
        "ACTUALS_FACT": {"ACTUALS_ID", "DATE", "AMOUNT"},
    }
    res = resolve_dataset_to_physical_table("Date", spend_date_cols, live_meta)
    assert res == "SPEND_DATE"


def test_collision_blocker_triggers_on_low_compat():
    live_meta = {
        "SALES": {"RANDOM_A", "RANDOM_B"},
    }
    model_cols = ["Opportunity ID", "Sales Date", "ProductRevenue"]
    blocker = check_table_name_collision_blocker("Sales", model_cols, live_meta)
    assert blocker is not None
    assert "already exists in Snowflake but its columns do not match" in blocker["message"]


def test_sql_guard_absent_table_enforcement():
    clear_allowed_absent_tables()
    # 1. Blocked when not registered
    with pytest.raises(SQLSecurityGuardError):
        validate_safe_sql('CREATE TABLE IF NOT EXISTS "SEMABRIDGE"."WORKSPACE"."ACCOUNTS" (ID INT);')

    # 2. Permitted when registered
    set_allowed_absent_tables({"ACCOUNTS"})
    validate_safe_sql('CREATE TABLE IF NOT EXISTS "SEMABRIDGE"."WORKSPACE"."ACCOUNTS" (ID INT);')

    # 3. Blocked if different table name
    with pytest.raises(SQLSecurityGuardError):
        validate_safe_sql('CREATE TABLE IF NOT EXISTS "SEMABRIDGE"."WORKSPACE"."OTHER" (ID INT);')

    # 4. Blocked if CTAS
    with pytest.raises(SQLSecurityGuardError):
        validate_safe_sql('CREATE TABLE IF NOT EXISTS "SEMABRIDGE"."WORKSPACE"."ACCOUNTS" AS SELECT 1;')

    # 5. Blocked if ALTER TABLE
    with pytest.raises(SQLSecurityGuardError):
        validate_safe_sql('ALTER TABLE "SEMABRIDGE"."WORKSPACE"."ACCOUNTS" ADD COLUMN X INT;')

    # 6. Blocked if DROP TABLE
    with pytest.raises(SQLSecurityGuardError):
        validate_safe_sql('DROP TABLE "SEMABRIDGE"."WORKSPACE"."ACCOUNTS";')

    clear_allowed_absent_tables()
