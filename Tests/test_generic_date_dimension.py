from semabridge.converter.dax_ast_parser import try_ast_translate, detect_date_tables
from semabridge.utils.naming import to_alias


def test_calendar_renamings_render_on_own_alias():
    # Test 1: same measure, calendar renamed - all must render on the calendar's own alias
    for cal in ("Calendar", "Posting_Date", "Kalender", "Fiscal Periods", "X7q9_Dims_Cal"):
        dax = f"CALCULATE(SUM(Sales[Amount]), SAMEPERIODLASTYEAR('{cal}'[Day]))"
        alias = to_alias(cal)
        table_aliases = {cal: alias, "Sales": "SALES"}
        sql = try_ast_translate(
            dax=dax,
            table_alias="SALES",
            primary_table_name="Sales",
            table_aliases=table_aliases,
        )
        assert sql is not None, f"SQL was None for {cal}"
        assert f'{alias.upper()}."DAY"' in sql.upper(), f"Expected {alias.upper()}.\"DAY\" in SQL, got: {sql}"
        assert 'COL_DATE."DAY"' not in sql.upper(), f"Unexpected COL_DATE.\"DAY\" in SQL: {sql}"


def test_fact_table_with_date_fk_not_detected_as_date_table():
    class MockCol:
        def __init__(self, name, dt="INTEGER", is_key=False, is_unique=False):
            self.unique_name = name
            self.data_type = dt
            self.is_key = is_key
            self.is_unique = is_unique

    class MockDataset:
        def __init__(self, name, cols, is_date_table=False, data_category=""):
            self.unique_name = name
            self.columns = cols
            self.is_date_table = is_date_table
            self.data_category = data_category

    class MockRel:
        def __init__(self, from_ds, from_col, to_ds, to_col, is_active=True):
            self.from_dataset = from_ds
            self.from_columns = [from_col]
            self.to_dataset = to_ds
            self.to_columns = [to_col]
            self.is_active = is_active

    # Sales fact with order_date FK pointing to Calendar date PK
    sales_ds = MockDataset("Sales", [
        MockCol("SalesID", "INTEGER", is_key=True),
        MockCol("OrderDate", "DATE"),
        MockCol("Amount", "DECIMAL"),
    ])

    calendar_ds = MockDataset("Posting_Date", [
        MockCol("DateKey", "DATE", is_key=True, is_unique=True),
        MockCol("Day", "INTEGER"),
    ])

    customer_ds = MockDataset("Customer", [
        MockCol("CustomerID", "INTEGER", is_key=True),
        MockCol("Name", "VARCHAR"),
    ])

    rel = MockRel("Sales", "OrderDate", "Posting_Date", "DateKey")
    datasets = [sales_ds, calendar_ds, customer_ds]
    relationships = [rel]
    measures = ["SUM(Sales[Amount])"]

    date_tables = detect_date_tables(datasets, relationships, measures)
    assert "sales" not in date_tables, "Sales fact table falsely classified as date table!"
    assert "customer" not in date_tables, "Customer table falsely classified as date table!"
    assert "posting_date" in date_tables, "Posting_Date table not detected as date table!"


def test_subtraction_of_aggregates_not_flagged_as_nested():
    from semabridge.connectors.type_safety_validator import detect_nested_aggregate
    sql = 'SUM(CASE WHEN SALES."MFG" = \'A\' THEN SALES."AMOUNT" END::FLOAT) - SUM(CASE WHEN SALES."MFG" = \'B\' THEN SALES."AMOUNT" END::FLOAT)'
    assert detect_nested_aggregate(sql) is None
