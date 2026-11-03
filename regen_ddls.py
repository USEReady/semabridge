from pathlib import Path
from semabridge.sml.serializer import SMLSerializer
from semabridge.connectors.snowflake_emitter import SnowflakeEmitter
from semabridge.core.settings import SnowflakeConfig

cfg = SnowflakeConfig(
    account="test",
    username="test",
    password="test",
    role="test",
    warehouse="test",
    database="test",
    schema="test",
)
emitter = SnowflakeEmitter(cfg)

models = {
    "HR_Sample": "output/reverse/proj-human-resource/sml/model.yaml",
    "Employee_Hiring": "output/reverse/proj-employee-hiring/sml/model.yaml",
}

for name, path_str in models.items():
    p = Path(path_str)
    if p.exists():
        sml = SMLSerializer.load(p)
        # In live deploy, fact tables needing time-intelligence are enriched
        if "Employee" in [d.unique_name for d in getattr(sml, "datasets", [])]:
            emitter._enriched_view_mapping["Employee"] = f"{cfg.database}.{cfg.schema}.EMPLOYEE_ENRICHED"
            emitter.semantic_view_builder.enriched_view_mapping = emitter._enriched_view_mapping
        ddl = emitter.semantic_view_builder._generate_semantic_view(sml)
        out_p = Path(f"output/debug/{name}.sql")
        out_p.write_text(ddl, encoding="utf-8")
        print(f"Regenerated {out_p}")
    else:
        print(f"Path does not exist: {p}")

