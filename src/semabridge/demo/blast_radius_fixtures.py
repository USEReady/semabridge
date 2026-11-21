"""Demo fixtures for Blast Radius simulations.

These mock reports and AI tools are used only during demo/mock simulations
when SEMABRIDGE_MOCK_DRIFT is enabled. They are isolated under semabridge.demo
so production code contains zero hardcoded demo tokens.
"""

MOCK_BI_REPORTS = [
    {
        "name": "Executive Marketing & Market Share Overview",
        "platform": "Power BI",
        "steward": "Sarah Jenkins (VP Commercial Analytics)",
        "details": "C-Suite strategic KPI scorecard monitoring corporate unit volume, net revenue, and competitor market share.",
        "metrics": {"TOTAL_UNITS", "TOTAL_UNITS_YTD", "PCT_UNITS_MARKET_SHARE", "SALES_DOL", "MARGIN_PCT", "KPI01", "KPI02"},
        "tables": {"SalesFact", "Product", "Date"},
        "columns": {"Units", "Revenue", "Discount", "Date", "Month", "Year"},
    },
    {
        "name": "Competitive Intelligence & Brand Benchmarking",
        "platform": "Power BI",
        "steward": "David Vance (Market Intelligence Lead)",
        "details": "Tracks VanArsdel brand dominance, competitor market penetration, and category growth waterfall.",
        "metrics": {"TOTAL_VANARSDEL_UNITS", "TOTAL_OTHER_UNITS", "TOTAL_VANARSDEL_UNITS_YTD", "TOTAL_OTHER_UNITS_YTD", "PCT_CATEGORY_COMPETE_SHARE", "TOTAL_CATEGORY_VOLUME", "TOTAL_COMPETE_VOLUME"},
        "tables": {"Product", "SalesFact", "Category"},
        "columns": {"isVanArsdel", "Manufacturer", "Category", "Units"},
    },
    {
        "name": "Regional Sales & Territory Performance Matrix",
        "platform": "Tableau",
        "steward": "Marcus Brody (Director of Sales Operations)",
        "details": "Geographic territory analysis tracking district dispatch volumes, regional growth variations, and quota attainment.",
        "metrics": {"TOTAL_UNITS", "TOTAL_UNITS_YTD", "TOTAL_UNITS_YTD_VAR", "TOTAL_UNITS_YTD_VAR_PCT", "TOTAL_CATEGORY_VOLUME"},
        "tables": {"Geo", "SalesFact", "Date"},
        "columns": {"Region", "District", "State", "Zip", "Units"},
    },
    {
        "name": "Product Portfolio Assortment & Sentiment Deep-Dive",
        "platform": "Power BI",
        "steward": "Elena Rostova (Product Line Strategy)",
        "details": "Assortment lifecycle breakdown linking active SKU counts, consumer sentiment scores, and unit momentum.",
        "metrics": {"COUNT_OF_PRODUCT", "SENTIMENT_SCORE", "TOTAL_UNITS", "PCT_UNITS_MARKET_SHARE_R12M"},
        "tables": {"Product", "Sentiment", "SalesFact"},
        "columns": {"Product", "Segment", "Score", "Units"},
    },
    {
        "name": "Commercial Revenue & Discount Audit Scorecard",
        "platform": "Tableau",
        "steward": "Robert Sterling (Commercial Finance Controller)",
        "details": "Financial reconciliation auditing gross-to-net leakage, promotional discounts, and margin realization.",
        "metrics": {"SALES_DOL", "MARGIN_PCT", "TOTAL_UNITS"},
        "tables": {"SalesFact", "Product"},
        "columns": {"Revenue", "Discount", "Units", "Status"},
    },
]

MOCK_AI_TOOLS = [
    {
        "name": 'Executive NL Assistant: "What is total unit volume by manufacturer?"',
        "steward": "Enterprise AI Steward",
        "details": "Cortex NL verified query driving leadership Teams & Slack bot.",
        "metrics": {"TOTAL_UNITS", "TOTAL_UNITS_YTD", "COUNT_OF_PRODUCT"},
        "tables": {"SalesFact", "Product"},
        "columns": {"Units", "Manufacturer"},
    },
    {
        "name": 'Competitive Market Share Agent: "Compare VanArsdel unit volume against competitors YTD"',
        "steward": "Commercial AI Steward",
        "details": "Automated weekly market share synthesis agent monitoring brand penetration.",
        "metrics": {"PCT_UNITS_MARKET_SHARE", "TOTAL_VANARSDEL_UNITS", "PCT_CATEGORY_COMPETE_SHARE"},
        "tables": {"SalesFact", "Product"},
        "columns": {"isVanArsdel", "Units"},
    },
    {
        "name": 'Pricing & Margin Insights Bot: "Summarize revenue and discount variance by region"',
        "steward": "Finance AI Steward",
        "details": "Financial forecast pipeline bot querying net revenue and promo discount delta.",
        "metrics": {"SALES_DOL", "MARGIN_PCT"},
        "tables": {"SalesFact", "Geo"},
        "columns": {"Revenue", "Discount", "Region"},
    },
]
