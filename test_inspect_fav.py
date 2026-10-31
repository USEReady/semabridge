import sys, os
sys.path.insert(0, "src")
from semabridge.converter.dax_translator import DAXTranslator
from semabridge.sml.models import SMLMetric

translator = DAXTranslator()
dax_fav = "SELECTEDVALUE('Opportunity Forecast Adjustment'[Forecast Adjustment], 0)"
res_fav = translator.translate(
    dax_fav,
    table_alias="opportunity_forecast_adjustment",
    dataset_name="Opportunity Forecast Adjustment",
    metric_name="Forecast Adjustment Value",
    dataset_aliases={"Opportunity Forecast Adjustment": "opportunity_forecast_adjustment"}
)
print("Forecast Adjustment Value:")
print("  Success:", res_fav.is_success)
print("  SQL:", res_fav.sql)
print("  Tier:", res_fav.tier)
print("  Failure:", res_fav.failure_reason)

dax_rip = """VAR Revenue =
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

metrics_ctx = [
    SMLMetric(
        unique_name="Forecast Adjustment Value",
        dataset="Opportunity Forecast Adjustment",
        expression=dax_fav,
        sql_expression=res_fav.sql
    )
]

res_rip = translator.translate(
    dax_rip,
    table_alias="opportunities",
    dataset_name="Opportunities",
    metric_name="Revenue In Pipeline",
    dataset_aliases={"Opportunities": "opportunities", "Opportunity Forecast Adjustment": "opportunity_forecast_adjustment"},
    metrics_context=metrics_ctx
)
print("\nRevenue In Pipeline (with translated context):")
print("  Success:", res_rip.is_success)
print("  SQL:", res_rip.sql)
print("  Tier:", res_rip.tier)
print("  Failure:", res_rip.failure_reason)

