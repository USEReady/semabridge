from semabridge.converter.dax_ast_parser import try_ast_translate
import logging

measure_map = {
    "Total Units": 'SUM(SALESFACT."Units")',
    "Total VanArsdel Units": "SUM(CASE WHEN PRODUCT.\"ISVANARSDEL\" = 'Yes' THEN SALESFACT.\"Units\" ELSE 0 END)",
    "Count of Product": "COUNT(PRODUCT.\"PRODUCT\")",
    "Sales $": 'SUM(SALESFACT."Revenue")',
    "Sentiment": "AVG(SENTIMENT.SCORE)",
    "Total Compete Volume": "SUM(CASE WHEN PRODUCT.\"ISVANARSDEL\" = 'No' THEN SALESFACT.\"Units\" ELSE 0 END)"
}

try:
    import sqlglot
    from sqlglot import exp
    for key, sql in measure_map.items():
        ast = sqlglot.parse_one(sql, read="snowflake")
        for identifier in ast.find_all(exp.Identifier):
            identifier.set("this", identifier.name.upper())
        print(key, ":", ast.sql(dialect="snowflake"))
except Exception as e:
    print(e)
