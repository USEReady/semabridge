import sqlglot
sql = 'DATE_TRUNC("COL_DATE"."YEAR", MAX_DATE)'
ast = sqlglot.parse_one(sql, read="snowflake")
print(ast.sql(dialect="snowflake"))
