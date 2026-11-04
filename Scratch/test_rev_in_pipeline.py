import sys, os
sys.path.insert(0, os.path.abspath("src"))

from semabridge.converter.dax_ast_parser import DaxAstParser, DaxSqlRenderer
from semabridge.converter.dax_translator import DAXTranslator
from semabridge.sml.models import SMLModel, SMLDataset, SMLColumn, SMLMetric, DataType

dax_measure = """VAR Revenue =
    CALCULATE (
        SUMX ( Opportunities, Opportunities[Value] ),
        FILTER (
            Opportunities,
            Opportunities[Status] = "Open"
            && VALUE(LEFT(Opportunities[PipelineStep],1)) >=2
        )
    )
RETURN
    Revenue + ( Revenue * ( 'Opportunity Forecast Adjustment'[Forecast Adjustment Value] / 100 ) )"""

print("=== 1. AST PARSING & DIRECT RENDERING ===")
parser = DaxAstParser()
ast = parser.parse(dax_measure)
print("AST root type:", type(ast).__name__)

# Case A: Without measure_sql_map
renderer = DaxSqlRenderer(
    table_alias="opportunities",
    primary_table_name="Opportunities",
    table_aliases={"Opportunities": "opportunities", "Opportunity Forecast Adjustment": "opportunity_forecast_adjustment"}
)
try:
    sql_a = renderer.render(ast)
    print("\nRendered SQL (Raw without measure map):\n", sql_a)
except Exception as e:
    print("\nRender failed without measure map:", e)

# Case B: With measure_sql_map containing Forecast Adjustment Value
renderer_b = DaxSqlRenderer(
    table_alias="opportunities",
    primary_table_name="Opportunities",
    table_aliases={"Opportunities": "opportunities", "Opportunity Forecast Adjustment": "opportunity_forecast_adjustment"},
    measure_sql_map={"Forecast Adjustment Value": "0", "Opportunity Forecast Adjustment[Forecast Adjustment Value]": "0"}
)
try:
    sql_b = renderer_b.render(ast)
    print("\nRendered SQL (With Forecast Adjustment Value inlined as 0):\n", sql_b)
except Exception as e:
    print("\nRender failed with measure map:", e)

print("\n=== 2. DAXTRANSLATOR.TRANSLATE ===")
translator = DAXTranslator()
res = translator.translate(
    dax_measure,
    table_alias="opportunities",
    dataset_name="Opportunities",
    metric_name="Revenue In Pipeline",
    dataset_aliases={"Opportunities": "opportunities", "Opportunity Forecast Adjustment": "opportunity_forecast_adjustment"},
    metrics_context=[
        SMLMetric(
            unique_name="Forecast Adjustment Value",
            dataset="Opportunity Forecast Adjustment",
            expression="SELECTEDVALUE('Opportunity Forecast Adjustment'[Forecast Adjustment], 0)",
            sql_expression="0"
        )
    ],
    skip_tier5=True
)
print("Translator Success:", res.is_success)
print("Translator Tier:", res.tier)
print("Translator SQL:\n", res.sql)
print("Translator Failure Reason:", res.failure_reason)

