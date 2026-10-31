from semabridge.connectors.local_pbix_connector import LocalPBIXConnector
from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.settings import SnowflakeConfig

pbix_path = r"C:\Users\manoj.s\Downloads\PBIX Files\Life expectancy v202009.pbix"
conn = LocalPBIXConnector({"pbix_path": pbix_path})
raw_tmsl = conn.discover().get("raw_tmsl") or {}
osi = TMSLToOSIConverter().to_osi({"tmsl": raw_tmsl, "dataset_id": "Life_expectancy"})
sml = OSIToSMLConverter().from_osi(osi)

cfg = SnowflakeConfig(account="test", user="test", password="pwd", warehouse="wh", database="SEMABRIDGE", schema_name="LIFE_EXPECTANCY")
emitter = SnowflakeEmitter(cfg)
from semabridge.connectors.tables_clause_builder import TablesClauseBuilder

tbuilder = TablesClauseBuilder(
    emitter.semantic_view_builder.identifier_sanitizer,
    emitter.semantic_view_builder.schema_manager,
    emitter.semantic_view_builder.config,
    emitter.semantic_view_builder.behavior,
    emitter.semantic_view_builder.live_schema_metadata,
)
registry = emitter.semantic_view_builder.alias_registry
tables_lines, declared_pk, rel_pk_map, ds_lookup, ds_by_name, live_ds_lookup = tbuilder.build_for_sml(
    sml, registry, {}, {"Years", "Indicators", "Population"}
)
print("declared_pk:", declared_pk)
for line in tables_lines:
    if "YEARS" in line:
        print("tables_line:", line)

