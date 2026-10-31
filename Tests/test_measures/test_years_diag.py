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

emitter.semantic_view_builder._current_model = sml
tbuilder = TablesClauseBuilder(
    emitter.semantic_view_builder.identifier_sanitizer,
    emitter.semantic_view_builder.schema_manager,
    emitter.semantic_view_builder.config,
    emitter.semantic_view_builder.behavior,
    emitter.semantic_view_builder.live_schema_metadata,
)
tbuilder._current_model = sml

for r in sml.relationships:
    if r.to_dataset == "Years":
        card = getattr(r, "cardinality", None)
        card_val = getattr(card, "value", card)
        card_str = str(card_val or "").lower().replace("_", "-")
        print("Rel:", r.from_dataset, "->", r.to_dataset, "card:", repr(card), "card_val:", repr(card_val), "card_str:", repr(card_str))
        years_ds = next(d for d in sml.datasets if d.unique_name == "Years")
        print("is_uniqueness_guaranteed:", tbuilder._is_uniqueness_guaranteed(years_ds, ["YEARS"], sml.relationships, "YEARS"))
        # Let's check step by step why it failed:
        target_cols = ["YEARS"]
        target_cols_upper = [c.upper() for c in target_cols]
        target_ds_names = {getattr(years_ds, "unique_name", None), getattr(years_ds, "name", None)} - {None}
        print("target_ds_names:", target_ds_names)
        print("r.to_dataset in target_ds_names:", r.to_dataset in target_ds_names)
        rel_to_cols = [
            tbuilder.schema_manager._resolve_physical_column_name(years_ds, c, model=sml).upper()
            if hasattr(tbuilder.schema_manager, "_resolve_physical_column_name")
            else tbuilder.identifier_sanitizer.sanitize_column(c).upper()
            for c in (getattr(r, "to_columns", None) or [])
        ]
        print("rel_to_cols:", rel_to_cols, "target_cols_upper:", target_cols_upper)
        print("rel_to_cols == target_cols_upper:", rel_to_cols == target_cols_upper)
        print("card_str in ('m:1', 'many-to-one', 'manytoone'):", card_str in ("m:1", "many-to-one", "manytoone"))
