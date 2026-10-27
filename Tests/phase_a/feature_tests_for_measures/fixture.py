"""
Synthetic star schema used by every feature test. Generic on purpose: no real model, table or
column names. Names include spaces / special characters so naming rules are exercised too.

  Orders  (fact)  -> Product (dimension) on ProductID
  Measures        empty measure-only table (only an ID column) - where many PBIX measures live
  Param           one-column what-if parameter table
"""
import datetime as dt
import pandas as pd

D = dt.date
ORDERS = pd.DataFrame({
    "OrderID":         [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
    "ProductID":       [1, 1, 2, 2, 3, 3, 1, 2, 3, 1],
    "Status":          ["Sold", "Sold", "Returned", "Sold", "Open", "Sold", "Returned", "Open", "Sold", "Sold"],
    "Qty":             [2, 5, 1, 3, 4, 2, 1, 6, 3, 2],
    "Price":           [10.0, 12.5, 8.0, 20.0, 15.0, 9.5, 11.0, 7.0, 30.0, 14.0],
    "Amount":          [20.0, 62.5, 8.0, 60.0, 60.0, 19.0, 11.0, 42.0, 90.0, 28.0],
    "Unit Price ($)":  [10.0, 12.5, 8.0, 20.0, 15.0, 9.5, 11.0, 7.0, 30.0, 14.0],
    "Order Date":      [D(2024, 1, 3), D(2024, 1, 10), D(2024, 2, 1), D(2024, 2, 15), D(2024, 3, 1),
                        D(2024, 3, 5), D(2024, 3, 20), D(2024, 4, 2), D(2024, 4, 9), D(2024, 4, 30)],
    "Ship Date":       [D(2024, 1, 5), D(2024, 1, 20), None, D(2024, 2, 18), None,
                        D(2024, 3, 9), D(2024, 3, 21), None, D(2024, 4, 19), D(2024, 5, 2)],
    "Is Priority":     [True, False, False, True, False, True, False, False, True, False],
    "Step":            ["3-Proposal", "1-Lead", "2-Qualify", "3-Proposal", "2-Qualify",
                        "1-Lead", "3-Proposal", "4-Close", "2-Qualify", "4-Close"],
})
PRODUCT = pd.DataFrame({"ProductID": [1, 2, 3], "Name": ["Alpha", "Beta", "Gamma"],
                        "Category": ["Hardware", "Software", "Software"]})
MEASURES = pd.DataFrame({"ID": [1]})
PARAM = pd.DataFrame({"Value": [0, 10, 20, 30]})

TABLES = {"Orders": ORDERS, "Product": PRODUCT, "Measures": MEASURES, "Param": PARAM}
FACT = "Orders"
RELATIONSHIPS = [("Orders", "ProductID", "Product", "ProductID")]   # many-to-one, active
