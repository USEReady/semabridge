#!/usr/bin/env python3
"""
Diagnostic Script: Measure Schema Mismatch Analysis

Shows why measures are being skipped and what columns are actually needed.
"""

# Measures from Competitive Marketing Analysis that are failing
FAILING_MEASURES = {
    "Total Units YTD": {
        "dax": "TOTALYTD([TOTAL UNITS], 'Date'[Date])",
        "required_columns": ["TOTAL_UNITS", "Date"],
        "reason": "References measure [TOTAL UNITS] which needs its own columns"
    },
    "Total VanArsdel Units YTD": {
        "dax": "CALCULATE([Total Units YTD], FILTER(ALL(Product[isVanArsdel]), Product[isVanArsdel]=\"Yes\"))",
        "required_columns": ["TOTAL_UNITS_YTD", "IS_VAN_ARSDEL"],
        "reason": "References measure [Total Units YTD]; Product table not available"
    },
    "Total VanArsdel Units": {
        "dax": "CALCULATE([Total Units], FILTER(ALL(Product[isVanArsdel]), Product[isVanArsdel]=\"Yes\"))",
        "required_columns": ["TOTAL_UNITS", "IS_VANARSDEL"],
        "reason": "Complex CALCULATE with FILTER on non-existent columns"
    },
    "% Units Market Share": {
        "dax": "IF([Total VanArsdel Units]=0, 0, DIVIDE([Total VanArsdel Units], [Total Units], 0))",
        "required_columns": ["TOTAL_UNITS", "AMOUNT"],
        "reason": "Uses measure references that don't exist"
    },
    "@Indicator01": {
        "dax": "IF('SalesFact'[% Category Compete Share]<0.55,1,IF('SalesFact'[% Category Compete Share]>0.6,3,2))",
        "required_columns": ["CATEGORY_COMPETE_SHARE"],
        "reason": "References computed measure as column"
    },
}

# Measures that COULD work with available columns
POTENTIAL_WORKING_MEASURES = {
    "Total Units": {
        "dax": "SUM([Units])",
        "requires": ["UNITS"],  # ✅ AVAILABLE
        "corrected_sql": "SUM(SALESFACT.UNITS)"
    },
    "Sales $": {
        "dax": "SUM([Revenue])",
        "requires": ["REVENUE"],  # ✅ AVAILABLE
        "corrected_sql": "SUM(SALESFACT.REVENUE)"
    },
    "Product Count": {
        "dax": "DISTINCTCOUNT([ProductID])",
        "requires": ["PRODUCTID"],  # ✅ AVAILABLE
        "corrected_sql": "COUNT(DISTINCT SALESFACT.PRODUCTID)"
    },
}

# Actual SalesFact schema
ACTUAL_SALESFACT_SCHEMA = ["DATE", "PRODUCTID", "REVENUE", "UNITS", "ZIP"]

print("=" * 100)
print("MEASURE SCHEMA MISMATCH DIAGNOSIS")
print("=" * 100)

print("\n📊 ACTUAL DATA SCHEMA (SalesFact table):")
print("-" * 100)
print(f"Available columns: {ACTUAL_SALESFACT_SCHEMA}")
print(f"Total physical columns: {len(ACTUAL_SALESFACT_SCHEMA)}")

print("\n❌ FAILING MEASURES ANALYSIS:")
print("-" * 100)
print(f"Total failing: {len(FAILING_MEASURES)}")
print("\nWhy they're failing:")
for measure, info in FAILING_MEASURES.items():
    print(f"\n• {measure}")
    print(f"  DAX: {info['dax'][:80]}...")
    print(f"  Status: ⚠️  SKIPPED")
    print(f"  Reason: {info['reason']}")
    missing = [col for col in info['required_columns'] if col not in ACTUAL_SALESFACT_SCHEMA]
    if missing:
        print(f"  Missing columns: {missing}")

print("\n✅ MEASURES THAT COULD WORK:")
print("-" * 100)
print(f"Total potentially working: {len(POTENTIAL_WORKING_MEASURES)}")
for measure, info in POTENTIAL_WORKING_MEASURES.items():
    all_available = all(col in ACTUAL_SALESFACT_SCHEMA for col in info['requires'])
    status = "✅ AVAILABLE" if all_available else "❌ MISSING"
    print(f"\n• {measure}")
    print(f"  DAX: {info['dax']}")
    print(f"  Requires: {info['requires']} - {status}")
    if all_available:
        print(f"  SQL: {info['corrected_sql']}")

print("\n" + "=" * 100)
print("ROOT CAUSE ANALYSIS")
print("=" * 100)

print("""
ISSUE 1: MEASURE INTERDEPENDENCIES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Many Fabric measures reference OTHER measures, creating a dependency chain:

  [Total Units YTD] references [TOTAL UNITS] (uses sanitized TOTAL_UNITS column)
    ↓
  [TOTAL UNITS] needs to be resolved first
    ↓
  But [TOTAL UNITS] might not exist as a separate measure

Solution: Use transitive DAX expansion or mark as dependent


ISSUE 2: SCHEMA MISMATCH
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Fabric model designed for rich schema:
  - Product table with isVanArsdel, Name, etc.
  - Sentiment table with Score, Gap
  - Date table with MonthIndex
  - Computed columns: % Category Compete Share, Brand Name, etc.

Test SalesFact table has minimal schema:
  - DATE, PRODUCTID, REVENUE, UNITS, ZIP only

This is 8 columns vs. 30+ expected.

Solution: Either:
  a) Use test data that matches Fabric schema enrichment level
  b) Filter measures to only ones that work with available columns
  c) Enrich the SalesFact table with missing dimensions


ISSUE 3: MEASURE TRANSLATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

When we translate TOTALYTD([TOTAL UNITS], Date[Date]):
  1. We extract [TOTAL UNITS] and assume it's a column
  2. We sanitize it to: TOTAL_UNITS
  3. We look for TOTAL_UNITS in SalesFact columns
  4. It's not there → WARNING: Column 'TOTAL_UNITS' not found


RECOMMENDATIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. SHORT TERM (Deploy what works now):
   ✅ Deploy only 3 working simple measures (Total Units, Sales $, Product Count)
   ✅ Verify semantic view creation succeeds
   ✅ Test queries work correctly

2. MEDIUM TERM (Improve compatibility):
   • Create extended test dataset with required columns
   • Add dimension tables (Product, Sentiment, Date)
   • Add computed columns (% Compete Share, Brand Name, etc.)

3. LONG TERM (Enhance translation):
   • Implement measure dependency graph
   • Expand measure references before SQL generation
   • Create mapping for multi-table semantics

""")

print("=" * 100)
print("NEXT STEPS")
print("=" * 100)
print("""
Option A: Deploy only compatible measures (RECOMMENDED FOR NOW)
────────────────────────────────────────────────────────────────
$ python main.py --model "Competitive Marketing Analysis" \\
    --filter-measures "[Total Units, Sales $, Product Count]" \\
    --deploy

Result: 3 queryable metrics + successful semantic view


Option B: Enrich data schema first
────────────────────────────────────────────────────────────────
1. Add Product dimension table with isVanArsdel flag
2. Add Sentiment table with sentiment scores
3. Run ETL to populate these enriched columns
4. Then deploy all 47 measures

Result: All 47 measures queryable in semantic view


Option C: Use simpler test model
────────────────────────────────────────────────────────────────
Create a "Test Simple" model with only basic measures:
  - Total Sales = SUM([Revenue])
  - Total Units = SUM([Units])
  - Avg Sale = AVG([Revenue])

Result: Cleaner testing without schema dependencies
""")
