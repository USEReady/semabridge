import sqlglot
sql = "CAST(REP_SFDC_TASK.DELTA_B_W_KPI_ORIGDATE_AND_LASTMODDATE AS FLOAT)"
ast = sqlglot.parse_one(sql, read="snowflake")
print("OUT:", ast.sql(dialect="snowflake"))
