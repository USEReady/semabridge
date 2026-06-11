import sqlglot
sql = "TRY_CAST(REP_SFDC_TASK.DELTA_B_W_KPI_ORIGDATE_AND_LASTMODDATE AS DOUBLE)"
ast = sqlglot.parse_one(sql, read="snowflake")
print(ast.sql(dialect="snowflake"))
