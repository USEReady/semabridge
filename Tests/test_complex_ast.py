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

print("Count of Product:", try_ast_translate("COUNT([Product])", "PRODUCT", "CALENDAR", measure_map))
print("Total VanArsdel Units YTD:", try_ast_translate("TOTALYTD([Total VanArsdel Units], 'Date'[Date])", "SalesFact", "CALENDAR", measure_map))
print("% Units Market Share:", try_ast_translate("DIVIDE([Total VanArsdel Units], [Total Units])", "SalesFact", "CALENDAR", measure_map))
print("Total Compete Volume:", try_ast_translate("CALCULATE([Total Units], 'Product'[IsCompete] = \"Yes\")", "SalesFact", "CALENDAR", measure_map))
