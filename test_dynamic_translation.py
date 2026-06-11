"""Full regression test for the dynamic translation engine refactor (with True Dynamic)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

PASS = 0
FAIL = 0

def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"PASS: {label}")
        PASS += 1
    else:
        print(f"FAIL: {label}" + (f" | {detail}" if detail else ""))
        FAIL += 1

# ---- imports ----
from semabridge.converter.dax_rule_translator import (
    rule_based_translation,
    _get_anchor,
    _get_column_mapping,
    translate_time_intelligence_with_anchors,
    translate_rolling_12_months,
    translate_sameperiodlastyear,
    translate_fiscal_cutoff,
    translate_vanarsdel_flag,
    translate_sentiment_gap,
)
from semabridge.converter.runtime_discovery import RuntimeColumnDiscovery
check("All imports OK", True)

# ---- RuntimeColumnDiscovery ----
class MockCol:
    def __init__(self, name):
        self.unique_name = name

class MockDataset:
    def __init__(self, name, cols):
        self.unique_name = name
        self.columns = cols

class MockModel:
    def __init__(self):
        self.datasets = [
            MockDataset("Product", [MockCol("IsVanArsdel"), MockCol("ProductID")]),
            MockDataset("Sentiment", [MockCol("Score"), MockCol("ReviewID")])
        ]

detection_patterns = {
    "vanarsdel_flag": {
        "column_keywords": ["vanarsdel", "isvanarsdel"],
        "table_keywords": ["product"]
    },
    "sentiment_score": {
        "column_keywords": ["score"],
        "table_keywords": ["sentiment"]
    }
}

discovered = RuntimeColumnDiscovery.discover_column_mappings(MockModel(), detection_patterns)
check("Runtime discovery finds VanArsdel table", discovered.get("vanarsdel_flag_table") == "Product")
check("Runtime discovery finds VanArsdel column", discovered.get("vanarsdel_flag_column") == "IsVanArsdel")
check("Runtime discovery finds Sentiment score", discovered.get("sentiment_score_column") == "Score")

# ---- _get_anchor ----
check("_get_anchor None fallback",  _get_anchor(None, "max_date_anchor", "MAX_DATE") == "MAX_DATE")
check("_get_anchor empty fallback", _get_anchor({},   "max_date_anchor", "MAX_DATE") == "MAX_DATE")
cfg = {"dynamic_anchors": {"max_date_anchor": "CUSTOM_MAX"}}
check("_get_anchor reads dynamic_anchors", _get_anchor(cfg, "max_date_anchor", "MAX_DATE") == "CUSTOM_MAX")

# ---- _get_column_mapping ----
check("_get_column_mapping None fallback", _get_column_mapping(None, "vanarsdel_flag_column", "ISVANARSDEL") == "ISVANARSDEL")
cfg2 = {"column_mappings": {"vanarsdel_flag_column": "MY_FLAG"}}
check("_get_column_mapping reads column_mappings", _get_column_mapping(cfg2, "vanarsdel_flag_column", "ISVANARSDEL") == "MY_FLAG")

# ---- translate_time_intelligence_with_anchors ----
dax_ytd = "TOTALYTD(SUM('Sales'[Amount]), 'Date'[Date])"
res_def = translate_time_intelligence_with_anchors(dax_ytd, "FACT")
res_cus = translate_time_intelligence_with_anchors(dax_ytd, "FACT", {"dynamic_anchors": {"max_date_anchor": "CUSTOM_MAX_DATE"}})
check("TOTALYTD default uses MAX_DATE",      res_def is not None and "MAX_DATE" in res_def, res_def)
check("TOTALYTD custom uses CUSTOM_MAX_DATE", res_cus is not None and "CUSTOM_MAX_DATE" in res_cus, res_cus)

# ---- translate_sameperiodlastyear ----
dax_sply = "CALCULATE([Revenue], SAMEPERIODLASTYEAR('Date'[Date]))"
res_sply = translate_sameperiodlastyear(dax_sply, "FACT", {"dynamic_anchors": {"max_date_anchor": "MY_MAX", "date_column": "MY_DATE_COL"}})
check("SAMEPERIODLASTYEAR uses config anchor+col",
      res_sply is not None and "MY_MAX" in res_sply and "MY_DATE_COL" in res_sply, res_sply)

# ---- translate_vanarsdel_flag ----
dax_va = "SUMX(FILTER(Sales, Product[isVanArsdel] = 'Yes'), [Units])"
res_va = translate_vanarsdel_flag(dax_va, "FACT", {
    "column_mappings": {"vanarsdel_flag_table": "MY_PROD", "vanarsdel_flag_column": "MY_FLAG", "units_fallback_column": "MY_UNITS"}
})
check("translate_vanarsdel_flag uses config",
      res_va is not None and "MY_PROD" in res_va and "MY_FLAG" in res_va, res_va)

# default must still use PRODUCT / ISVANARSDEL
res_va_def = translate_vanarsdel_flag(dax_va, "FACT")
check("translate_vanarsdel_flag default uses PRODUCT",
      res_va_def is not None and "PRODUCT" in res_va_def and "ISVANARSDEL" in res_va_def, res_va_def)

# ---- behavior.py new fields ----
from semabridge.core.behavior import SnowflakeDynamicConfig
cfg_obj = SnowflakeDynamicConfig()
check("SnowflakeDynamicConfig.detection_patterns is dict", isinstance(cfg_obj.detection_patterns, dict))
check("SnowflakeDynamicConfig.column_mappings is dict",    isinstance(cfg_obj.column_mappings, dict))

print()
print(f"Results: {PASS} passed, {FAIL} failed")
sys.exit(0 if FAIL == 0 else 1)
