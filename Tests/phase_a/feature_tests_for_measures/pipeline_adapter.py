"""
pipeline_adapter.py - calls SemaBridge's real pipeline stages. Wire once if your API differs; the runner
and cases never change. Every function gets/returns plain SemaBridge objects.

  to_osi(tmsl_model_dict, name)  -> OSI model      (TMSLToOSIConverter)
  to_sml(osi_model)              -> SML model      (OSIToSMLConverter; translation + dependency resolution)
  normalize(sml_model)           -> None           (relationship normalisation done before DDL; optional)
  generate_ddl(sml_model, live, database, schema) -> DDL text       (the CREATE SEMANTIC VIEW SemaBridge would deploy)
        live = {TABLE_NAME: {COLUMN, ...}} the physical columns that "exist in Snowflake" (from the fixture),
        so SemaBridge's column / alias validation runs exactly as in a real dry run.
"""


def to_osi(tmsl, name):
    from semabridge.converter.tmsl_to_osi import TMSLToOSIConverter
    conv = TMSLToOSIConverter()
    try:
        return conv.to_osi({"tmsl": tmsl, "dataset_id": name})
    except TypeError:
        return conv.to_osi(tmsl)


def to_sml(osi):
    from semabridge.converter.osi_to_sml import OSIToSMLConverter
    return OSIToSMLConverter().from_osi(osi)


def normalize(sml):
    try:
        from semabridge.core.engine.conversion import base
        fn = getattr(base, "_normalize_relationships_for_target", None)
        if fn:
            fn(type("E", (), {"_has_path": getattr(base, "_has_path", None),
                              "_select_strongest_relationship": getattr(base, "_select_strongest_relationship", None)})(), sml)
    except Exception as exc:
        print(f"  (normalize skipped: {exc})")


def generate_ddl(sml, live, database=None, schema=None):
    from semabridge.core.settings import SnowflakeConfig
    from semabridge.connectors.snowflake_emitter import SnowflakeEmitter

    cfg = SnowflakeConfig(account="dummy", user="dummy", password="dummy", database=database or "DB", schema=schema or "SC")
    emitter = SnowflakeEmitter(cfg)
    if live:
        for t, cols in live.items():
            emitter._live_schema_metadata[t] = set(cols)
    ddls = emitter.generate_ddls(sml)
    return ddls[0] if isinstance(ddls, (list, tuple)) else ddls

