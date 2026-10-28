"""
Extra synthetic tables for the cross-table features (F11-F15). Generic names only.

  Calendar  date dimension. Orders[Order Date] -> Calendar[Cal Date] is ACTIVE,
            Orders[Ship Date] -> Calendar[Cal Date] is INACTIVE (needs an alias / USERELATIONSHIP)
  Period    period dimension for snapshot data
  Snapshot  one row per employee per period (closing-balance / non-additive data)
"""
import datetime as dt
import pandas as pd


def extend(TABLES, RELATIONSHIPS, INACTIVE):
    start = dt.date(2024, 1, 1)
    days = [start + dt.timedelta(days=i) for i in range(152)]            # Jan..May 2024
    TABLES["Calendar"] = pd.DataFrame({"Cal Date": days, "Month No": [d.month for d in days],
                                       "Year No": [d.year for d in days]})
    TABLES["Period"] = pd.DataFrame({"Period No": [1, 2, 3]})
    TABLES["Snapshot"] = pd.DataFrame({
        "Emp ID":    [1, 2, 3, 1, 2, 3, 4, 1, 3, 4, 5],
        "Period No": [1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3],
        "End Date":  [None, None, None, None, dt.date(2024, 2, 20), None, None, None, None, None, dt.date(2024, 3, 25)],
    })
    RELATIONSHIPS.append(("Orders", "Order Date", "Calendar", "Cal Date"))
    RELATIONSHIPS.append(("Snapshot", "Period No", "Period", "Period No"))
    INACTIVE.append(("Orders", "Ship Date", "Calendar", "Cal Date"))
