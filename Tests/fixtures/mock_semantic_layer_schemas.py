"""Mock schemas and relationships for testing semantic layer translation."""

MOCK_TABLE_SCHEMAS = {
    "SalesFact": [
        "DATE",
        "PRODUCTID",
        "REVENUE",
        "UNITS",
        "ZIP",
        "QUANTITY",
        "COST",
    ],
    "Product": [
        "PRODUCTID",
        "ISVANARSDEL",
        "PRODUCTNAME",
        "CATEGORY",
        "MANUFACTURER",
    ],
    "Date": [
        "DATE",
        "MONTHINDEX",
        "YEAR",
        "MONTH",
        "DAYOFWEEK",
        "QUARTER",
    ],
    "Sentiment": [
        "PRODUCTID",
        "SCORE",
        "SENTIMENT_TEXT",
    ],
    "Manufacturer": [
        "MFGISVANARSDEL",
        "MFGNAME",
    ],
}

MOCK_RELATIONSHIPS = [
    ("SalesFact.PRODUCTID", "Product.PRODUCTID"),
    ("SalesFact.DATE", "Date.DATE"),
    ("SalesFact.PRODUCTID", "Sentiment.PRODUCTID"),
    ("Product.MANUFACTURER", "Manufacturer.MFGNAME"),
]
