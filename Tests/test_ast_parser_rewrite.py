from semabridge.converter.dax_ast_parser import try_ast_translate
import logging
logging.basicConfig(level=logging.DEBUG)

sql = try_ast_translate(
    dax="TOTALYTD([TOTAL UNITS], 'Date'[Date])", 
    table_alias="SalesFact", 
    date_alias="CALENDAR", 
    measure_sql_map={"TOTAL UNITS": "SUM(Units)"}
)
print("TEST 1 (TOTALYTD):", sql)

sql2 = try_ast_translate(
    dax="CALCULATE([TOTAL UNITS], FILTER(ALL('Date'), 'Date'[Year] = 2024))", 
    table_alias="SalesFact", 
    date_alias="CALENDAR", 
    measure_sql_map={"TOTAL UNITS": "SUM(Units)"}
)
print("TEST 2 (CALCULATE FILTER ALL):", sql2)

sql3 = try_ast_translate(
    dax="CALCULATE([TOTAL UNITS], SAMEPERIODLASTYEAR('Date'[Date]))", 
    table_alias="SalesFact", 
    date_alias="CALENDAR", 
    measure_sql_map={"TOTAL UNITS": "SUM(Units)"}
)
print("TEST 3 (CALCULATE SAMEPERIODLASTYEAR):", sql3)
