# DAX Translation Pipeline

Semabridge provides a deterministic DAX-to-SQL translation pipeline for
Power BI/Fabric measures.

## What It Does
- Parses DAX measure definitions.
- Maps DAX column references to schema columns.
- Generates deterministic SQL expressions.
- Falls back to optional LLM translation for complex cases.

## Implementation References
- Pipeline: src/semabridge/converter/dax_pipeline.py
- Parsing: src/semabridge/converter/dax_parser.py
- Dictionary: src/semabridge/converter/measure_dictionary.py
- SQL generation: src/semabridge/converter/dax_sql_generator.py
- Optional LLM: src/semabridge/converter/llm_dax_translator.py

## Typical Usage (Python)

```
from semabridge.converter.dax_pipeline import DaxTranslationPipeline

pipeline = DaxTranslationPipeline(schema_columns=["AMOUNT", "UNITS"])
pipeline.add_column_mappings({"Amount": "AMOUNT", "Units": "UNITS"})

pipeline.load_measures_from_dax("MEASURE 'Sales'[Total] = SUM([Amount])")
results = pipeline.resolve_all()
```
