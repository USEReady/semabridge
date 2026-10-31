import sys, os, re
sys.path.insert(0, ".")
sys.path.insert(0, "Tests/phase_a")
from validate_semantic_view import _clause, _split_top
from relationship_inventory import sb_column

ddl = open(r'output\debug\debug_generated_sql_Competitive_Marketing_Analysis.sql', encoding='utf-8').read()
metrics_clause = _clause(ddl, 'metrics')
items = _split_top(metrics_clause)

emitted = {}
for item in items:
    m = re.match(r'^\s*("?[^".\s]+"?)\s*\.\s*("(?:[^"]|"")+"|[^\s]+)\s+as\s+(.*)$', item, re.I | re.S)
    if m:
        t = m.group(1).strip('"')
        name = m.group(2).strip('"')
        expr = m.group(3).strip()
        emitted[name.upper()] = (t, expr, 'CAST(NULL AS' in expr.upper())

measures_input = [
    ("Product", "Count of Product", "COUNTA('Product'[Product])"),
    ("Product", "NewCount of Product", "COUNTBLANK('Product'[Product])"),
    ("Product", "NewNewCount of Product", "COUNTBLANK('Product'[Product])"),
    ("SalesFact", "Total Units", "SUM([Units])"),
    ("SalesFact", "Sales $", "SUM([Revenue])"),
    ("SalesFact", "Sentiment", "AVERAGE(Sentiment[Score])"),
    ("SalesFact", "Total Units YTD", "TOTALYTD([TOTAL UNITS], 'Date'[Date])"),
    ("SalesFact", "Total VanArsdel Units YTD", "CALCULATE([Total Units YTD], FILTER(ALL(Product[isVanArsdel]), Product[isVanArsdel]=\"Yes\"))"),
    ("SalesFact", "Total OTHER Units YTD", "CALCULATE([Total Units YTD], FILTER(ALL(Product[isVanArsdel]), Product[isVanArsdel]=\"No\"))"),
    ("SalesFact", "Total VanArsdel Units", "CALCULATE([Total Units], FILTER(ALL(Product[isVanArsdel]), Product[isVanArsdel]=\"Yes\"))"),
    ("SalesFact", "Total OTHER Units", "CALCULATE([Total Units], FILTER(ALL(Product[isVanArsdel]), Product[isVanArsdel]=\"No\"))"),
    ("SalesFact", "% Units Market Share", "IF([Total VanArsdel Units]=0, 0, DIVIDE([Total VanArsdel Units], [Total Units], 0))"),
    ("SalesFact", "Total Category Volume", "CALCULATE([Total Units])"),
    ("SalesFact", "Total Compete Volume", "CALCULATE([Total Units], FILTER('Product', Product[isVanArsdel]=\"No\"))"),
    ("SalesFact", "% Category Compete Share", "INT(DIVIDE([Total Compete Volume], [Total Category Volume])*100)"),
    ("SalesFact", "TOTAL UNITS SPLY", "CALCULATE([TOTAL UNITS],SAMEPERIODLASTYEAR('Date'[Date]))"),
    ("SalesFact", "Total Units YTD SPLY", "CALCULATE([Total Units YTD], SAMEPERIODLASTYEAR('Date'[Date]))"),
    ("SalesFact", "Total VanArsdel Units YTD SPLY", "CALCULATE([Total VanArsdel Units YTD], SAMEPERIODLASTYEAR('Date'[Date]))"),
    ("SalesFact", "Total OTHER Units YTD SPLY", "CALCULATE([Total OTHER Units YTD], SAMEPERIODLASTYEAR('Date'[Date]))"),
    ("SalesFact", "% Units Market Share YTD", "DIVIDE([Total VanArsdel Units YTD], [Total Units YTD])"),
    ("SalesFact", "% Market Share SPLY YTD", "CALCULATE([% Units Market Share YTD], SAMEPERIODLASTYEAR('Date'[Date]))"),
    ("SalesFact", "% Unit Market Share YOY Change", "[% Units Market Share]-[% Units Market Share SPLY]"),
    ("SalesFact", "% Units Market Share SPLY", "CALCULATE([% Units Market Share], SAMEPERIODLASTYEAR('Date'[Date]))"),
    ("SalesFact", "Total Units R12Ms", "CALCULATE(SUM([Units]), FILTER(ALL('Date'), 'Date'[MonthIndex]<=MAX('Date'[MonthIndex])&& 'Date'[MonthIndex]> MAX('Date'[MonthIndex])-12))"),
    ("SalesFact", "Total VanArsdel Units R12M", "CALCULATE([Total Units R12Ms], FILTER(ALL(Product[isVanArsdel]), Product[isVanArsdel]=\"Yes\"))"),
    ("SalesFact", "Total OTHER Units R12M", "CALCULATE([Total Units R12Ms], FILTER(ALL(Product[isVanArsdel]), Product[isVanArsdel]=\"No\"))"),
    ("SalesFact", "% Units Market Share R12M", "IF([Total VanArsdel Units R12M]=0, 0, DIVIDE([Total VanArsdel Units R12M], [Total Units R12Ms], 0))"),
    ("SalesFact", "Sentiment Gap", "IF(ISBLANK(CALCULATE([Sentiment], Manufacturer[MfgisVanArsdel]=\"No\"))||ISBLANK(CALCULATE([Sentiment], Manufacturer[MfgisVanArsdel]=\"Yes\")), BLANK(), CALCULATE([Sentiment], Manufacturer[MfgisVanArsdel]=\"No\") - CALCULATE([Sentiment], Manufacturer[MfgisVanArsdel]=\"Yes\"))"),
    ("SalesFact", "Total Units YTD Var", "[Total Units YTD]-[Total Units YTD SPLY]"),
    ("SalesFact", "Total Units YTD Var %", "DIVIDE([Total Units YTD Var],[TOTAL UNITS SPLY])"),
    ("SalesFact", "Sum of Units", "SUM('SalesFact'[Units])"),
    ("SalesFact", "Sum of Revenue", "SUM('SalesFact'[Revenue])"),
    ("SalesFact", "@Indicator01", "IF('SalesFact'[% Category Compete Share]<0.55,1,IF('SalesFact'[% Category Compete Share]>0.6,3,2))"),
    ("SalesFact", "@Indicator02", "IF('SalesFact'[% Unit Market Share YOY Change]<0,1,IF('SalesFact'[% Unit Market Share YOY Change]>.2,3,2))"),
    ("SalesFact", "#Space01", "\"\""),
    ("SalesFact", "#Space02", "\"\""),
    ("SalesFact", "@Indicator03", "IF('SalesFact'[Total Units YTD Var %]<0,1,IF('SalesFact'[Total Units YTD Var %]>0.1,3,2))"),
    ("SalesFact", "@Indicator04", "IF('SalesFact'[Sentiment]<65,1,IF('SalesFact'[Sentiment]>67,3,2))"),
    ("SalesFact", "@Indicator05", "IF('SalesFact'[Sentiment Gap]<15,1,IF('SalesFact'[Sentiment Gap]>25,3,2))"),
    ("SalesFact", "@Indicator04A", "IF('SalesFact'[@Indicator04]=1,\"Low Sentiment Rate\",IF('SalesFact'[@Indicator04]=3,\"High Sentiment Rate\",\"Medium Sentiment Rate\"))"),
    ("SalesFact", "@Indicator05A", "IF('SalesFact'[@Indicator05]=1,\"Low Sentiment Gap\",IF('SalesFact'[@Indicator05]=3,\"High Sentiment Gap\",\"Medium Sentiment Gap\"))"),
    ("SalesFact", "Total Units YTD Var %2", "INT(DIVIDE([Total Units YTD Var],[TOTAL UNITS SPLY])*100)"),
    ("Sentiment", "Sum of Score", "SUM('Sentiment'[Score])"),
    ("Sentiment", "Average of Score", "AVERAGE('Sentiment'[Score])"),
    ("Sentiment", "New Demo Measure", "AVERAGE('Sentiment'[Score])"),
    ("KPI", "KPI01", "IF(SUM('KPI'[KPI])=1,CONCATENATE(LEFT([Total Units YTD Var %2],3),(\" % ROI (Return on Investment)\")),IF(SUM('KPI'[KPI])=2,\" \",IF(SUM('KPI'[KPI])=3,CALCULATE([Total Category Volume],'Date'[Running Year]=1))))"),
    ("KPI", "KPI02", "IF(SUM('KPI'[KPI])=1,(CALCULATE([Total Category Volume],'Date'[Running Year]=1)),IF(SUM('KPI'[KPI])=2,\" \",IF(SUM('KPI'[KPI])=3,CONCATENATE(LEFT([Total Units YTD Var %2],3),\"%\"))))"),
]

print(f"{'MEASURE':<35} | {'STATUS':<15} | {'DETAIL'}")
print("-" * 80)
for tbl, name, expr in measures_input:
    k = sb_column(name).upper().strip('"')
    if k in emitted:
        t, sq, is_null = emitted[k]
        if is_null:
            print(f"{name:<35} | NULL_CAST       | {sq[:40]}")
        else:
            print(f"{name:<35} | REAL_SQL        | {sq[:40]}")
    else:
        # Check by_design
        if expr.strip() in ['""', "''"] or 'CONCATENATE' in expr or 'Low Sentiment' in expr:
            print(f"{name:<35} | EXCLUDED (Text) | By design excluded (String / Blank)")
        else:
            print(f"{name:<35} | NOT IN DDL      | Check mapping/table")
