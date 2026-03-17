# API Reference - DAX Translation Components

## Table of Contents

1. [DaxTranslationPipeline](#daxtranslationpipeline)
2. [MeasureDefinition](#measuredefinition)
3. [MeasureDictionary](#measuredictionary)
4. [DeterministicSQLGenerator](#deterministicsqlgenerator)
5. [DaxExpressionParser](#daxexpressionparser)
6. [MeasureDefinitionExtractor](#measuredefinitionextractor)

---

## DaxTranslationPipeline

Main orchestrator for DAX translation workflow.

### Initialization

```python
from semabridge.converter.dax_pipeline import DaxTranslationPipeline

pipeline = DaxTranslationPipeline(schema_columns: Optional[List[str]] = None)
```

**Parameters:**
- `schema_columns` (Optional[List[str]]): List of column names available in the database schema

**Example:**
```python
pipeline = DaxTranslationPipeline(
    schema_columns=['AMOUNT', 'UNITS', 'DATE', 'CATEGORY']
)
```

### Methods

#### load_measures_from_dax

Load and parse DAX measure definitions.

```python
def load_measures_from_dax(dax_text: str) -> int
```

**Parameters:**
- `dax_text` (str): Text containing MEASURE statements from Power BI

**Returns:**
- `int`: Number of measures loaded

**Example:**
```python
dax = """
MEASURE 'Sales'[Total Revenue] = SUM('Sales'[Amount])
MEASURE 'Sales'[Total Units] = SUM('Sales'[Units])
"""
n = pipeline.load_measures_from_dax(dax)
print(f"Loaded {n} measures")
```

**Raises:**
- `ValueError`: If DAX text is empty

#### add_column_mapping

Add a single column mapping from DAX to schema.

```python
def add_column_mapping(
    dax_column: str, 
    schema_column: str, 
    confidence: float = 1.0
) -> None
```

**Parameters:**
- `dax_column` (str): Column name as referenced in DAX (e.g., "Amount")
- `schema_column` (str): Actual column name in database schema (e.g., "AMOUNT")
- `confidence` (float): Confidence in mapping (0.0 to 1.0), default 1.0

**Example:**
```python
pipeline.add_column_mapping('Amount', 'AMOUNT', confidence=1.0)
pipeline.add_column_mapping('Units', 'UNITS', confidence=0.95)
```

#### add_column_mappings

Add multiple column mappings at once.

```python
def add_column_mappings(mappings: Dict[str, str]) -> None
```

**Parameters:**
- `mappings` (Dict[str, str]): Dictionary of {dax_column: schema_column}

**Example:**
```python
pipeline.add_column_mappings({
    'Amount': 'AMOUNT',
    'Units': 'UNITS',
    'Cost': 'COST',
    'Date': 'DATE',
})
```

#### set_schema_columns

Set available schema columns.

```python
def set_schema_columns(columns: List[str]) -> None
```

**Parameters:**
- `columns` (List[str]): List of column names in database schema

**Example:**
```python
pipeline.set_schema_columns(['AMOUNT', 'UNITS', 'DATE'])
```

#### resolve_all

Resolve all measures and generate SQL.

```python
def resolve_all() -> Dict[str, Optional[str]]
```

**Returns:**
- `Dict[str, Optional[str]]`: Dictionary of {measure_name: sql_expression or None}

**Example:**
```python
results = pipeline.resolve_all()
for measure_name, sql in results.items():
    if sql:
        print(f"✓ {measure_name}: {sql}")
    else:
        print(f"✗ {measure_name}: Failed")
```

#### translate

Get SQL for a specific measure.

```python
def translate(measure_name: str, table_alias: str = "fact") -> Optional[str]
```

**Parameters:**
- `measure_name` (str): Name of measure to translate
- `table_alias` (str): SQL table alias, default "fact"

**Returns:**
- `Optional[str]`: SQL expression or None if translation failed

**Example:**
```python
sql = pipeline.translate("Total Revenue", table_alias="sales")
if sql:
    print(f"SQL: {sql}")
else:
    print("Translation failed")
```

#### get_failed_measures

Get list of measures that failed to resolve.

```python
def get_failed_measures() -> List[Tuple[str, str]]
```

**Returns:**
- `List[Tuple[str, str]]`: List of (measure_name, error_reason) tuples

**Example:**
```python
failed = pipeline.get_failed_measures()
for measure_name, error in failed:
    print(f"{measure_name}: {error}")
```

#### get_statistics

Get statistics about the translation.

```python
def get_statistics() -> Dict
```

**Returns:**
- `Dict`: Statistics dictionary with keys:
  - `total_measures` (int)
  - `deterministic_translated` (int)
  - `failures` (int)
  - `deterministic_rate` (str): e.g., "90.5%"
  - `llm_reduction` (str): e.g., "90.5%"
  - `average_time_ms` (str): e.g., "45.2"
  - `success_rate` (str): e.g., "95.0%"
  - `failed_measures` (Dict): Optional, if failures exist

**Example:**
```python
stats = pipeline.get_statistics()
print(f"Success rate: {stats['success_rate']}")
print(f"Deterministic: {stats['deterministic_rate']}")
print(f"LLM reduction: {stats['llm_reduction']}")
```

#### export_measures

Export all measures for debugging.

```python
def export_measures() -> Dict
```

**Returns:**
- `Dict`: Dictionary containing measures, mappings, and schema columns

**Example:**
```python
export = pipeline.export_measures()
print(f"Total mappings: {len(export['column_mappings'])}")
```

#### validate

Validate pipeline configuration.

```python
def validate() -> Tuple[bool, List[str]]
```

**Returns:**
- `Tuple[bool, List[str]]`: (is_valid, error_messages)

**Example:**
```python
is_valid, errors = pipeline.validate()
if not is_valid:
    for error in errors:
        print(f"Error: {error}")
```

### Properties

#### translation_cache

Dictionary of cached translations.

```python
cache: Dict[str, Optional[str]]
```

**Example:**
```python
if measure_name in pipeline.translation_cache:
    sql = pipeline.translation_cache[measure_name]
```

#### dictionary

Internal measure dictionary.

```python
dictionary: MeasureDictionary
```

#### generator

Internal SQL generator.

```python
generator: DeterministicSQLGenerator
```

#### stats

Current statistics.

```python
stats: TranslationStats
```

---

## MeasureDefinition

Data class representing a DAX measure.

```python
from dataclasses import dataclass
from semabridge.converter.dax_parser import MeasureDefinition

@dataclass
class MeasureDefinition:
    name: str              # "Total Revenue"
    table: str             # "Sales"
    expression: DaxExpression
    line_number: int       # Line in DAX file
    raw: str               # Original DAX text
```

### Properties

#### full_name

Fully qualified measure name.

```python
measure.full_name  # "Sales'[Total Revenue]"
```

#### key

Unique identifier for measure.

```python
measure.key  # ("Sales", "Total Revenue")
```

---

## MeasureDictionary

Manages collections of measures and column mappings.

```python
from semabridge.converter.measure_dictionary import MeasureDictionary

dictionary = MeasureDictionary(schema_columns: Optional[List[str]] = None)
```

### Methods

#### add_measure

Add a single measure.

```python
def add_measure(measure: MeasureDefinition) -> None
```

#### add_measures

Add multiple measures.

```python
def add_measures(measures: List[MeasureDefinition]) -> None
```

#### add_column_mapping

Add a column mapping.

```python
def add_column_mapping(mapping: ColumnMapping) -> None
```

#### get_measure

Get a measure by name.

```python
def get_measure(name: str) -> Optional[MeasureDefinition]
```

**Example:**
```python
measure = dictionary.get_measure("Total Revenue")
if measure:
    print(f"Expression: {measure.expression}")
```

#### resolve_all

Resolve all measures in dependency order.

```python
def resolve_all() -> Dict[str, Optional[str]]
```

#### set_schema_columns

Set available schema columns.

```python
def set_schema_columns(columns: List[str]) -> None
```

#### get_resolution_error

Get reason why a measure failed to resolve.

```python
def get_resolution_error(measure_name: str) -> Optional[str]
```

---

## DeterministicSQLGenerator

Generates SQL from DAX expressions.

```python
from semabridge.converter.dax_sql_generator import DeterministicSQLGenerator

generator = DeterministicSQLGenerator(
    schema_columns: Optional[Set[str]] = None
)
```

### Methods

#### translate

Translate a DAX expression to SQL.

```python
def translate(
    dax_expression: str,
    table_alias: str = "fact",
    measure_name: str = "Unknown"
) -> Optional[str]
```

**Parameters:**
- `dax_expression` (str): DAX expression to translate
- `table_alias` (str): SQL table alias
- `measure_name` (str): Measure name for logging

**Returns:**
- `Optional[str]`: SQL expression or None

**Example:**
```python
sql = generator.translate(
    "SUM([Units])",
    table_alias="sales",
    measure_name="Total Units"
)
# Returns: "SUM(sales.UNITS)"
```

#### translate_expression

Low-level expression translation.

```python
def translate_expression(expr: DaxExpression) -> Optional[str]
```

### Properties

#### column_mappings

Column mapping dictionary.

```python
generator.column_mappings: Dict[str, str]
```

**Example:**
```python
generator.column_mappings['UNITS'] = 'UNITS'
generator.column_mappings['AMOUNT'] = 'AMOUNT'
```

#### schema_columns

Available schema columns.

```python
generator.schema_columns: Set[str]
```

---

## DaxExpressionParser

Parses DAX expressions.

```python
from semabridge.converter.dax_parser import DaxExpressionParser

parser = DaxExpressionParser()
expr = parser.parse("SUM([Units])")
```

### Methods

#### parse

Parse a DAX expression.

```python
def parse(expression_str: str) -> DaxExpression
```

**Returns:**
- `DaxExpression`: Parsed expression object

**Example:**
```python
expr = parser.parse("DIVIDE([Revenue], [Units])")
print(f"Function: {expr.function}")
print(f"Columns: {expr.columns}")
```

#### extract_columns

Extract column references from expression.

```python
def extract_columns(expression_str: str) -> List[str]
```

**Example:**
```python
columns = parser.extract_columns("SUM([Amount] + [Tax])")
# Returns: ["Amount", "Tax"]
```

#### extract_measures

Extract measure references from expression.

```python
def extract_measures(expression_str: str) -> List[str]
```

---

## MeasureDefinitionExtractor

Extracts measures from DAX text.

```python
from semabridge.converter.dax_parser import MeasureDefinitionExtractor

extractor = MeasureDefinitionExtractor()
measures = extractor.extract_all(dax_text)
```

### Methods

#### extract_all

Extract all measures from DAX text.

```python
def extract_all(dax_text: str) -> List[MeasureDefinition]
```

**Returns:**
- `List[MeasureDefinition]`: List of extracted measures

**Example:**
```python
dax = """
MEASURE 'Sales'[Total] = SUM([Amount])
MEASURE 'Sales'[Count] = COUNTA([ID])
"""
measures = extractor.extract_all(dax)
for measure in measures:
    print(f"{measure.table}[{measure.name}]")
```

#### extract_one

Extract a single measure.

```python
def extract_one(dax_text: str, start_pos: int = 0) -> Optional[MeasureDefinition]
```

---

## Data Classes

### DaxExpression

```python
@dataclass
class DaxExpression:
    raw: str              # Original expression
    function: str         # "SUM", "DIVIDE", etc.
    arguments: List[str]  # Function arguments
    columns: List[str]    # Referenced columns
    measures: List[str]   # Referenced measures
```

### ColumnMapping

```python
@dataclass
class ColumnMapping:
    dax_column: str       # Column name in DAX
    schema_column: str    # Column name in schema
    confidence: float     # 0.0-1.0
    source: str           # "manual" or "inferred"
```

### TranslationStats

```python
@dataclass
class TranslationStats:
    total_measures: int = 0
    deterministic_translated: int = 0
    llm_required: int = 0
    failures: int = 0
    average_time_ms: float = 0.0
    deterministic_rate: str = "0%"
    llm_reduction: str = "0%"
    
    @property
    def success_rate(self) -> str: ...
```

---

## Exception Classes

### ColumnNotFoundError

Raised when a column is not in the schema.

```python
from semabridge.converter.dax_sql_generator import ColumnNotFoundError

try:
    sql = generator.translate("SUM([NonExistent])")
except ColumnNotFoundError as e:
    print(f"Column error: {e}")
```

### ColumnNotMappedError

Raised when a column mapping is missing.

```python
from semabridge.converter.measure_dictionary import ColumnNotMappedError

try:
    dict.resolve_all()
except ColumnNotMappedError as e:
    print(f"Mapping missing: {e}")
```

### CircularDependencyError

Raised when measures have circular dependencies.

```python
from semabridge.converter.measure_dictionary import CircularDependencyError

try:
    dict.resolve_all()
except CircularDependencyError as e:
    print(f"Circular dependency: {e}")
```

---

## Common Patterns

### Pattern 1: Basic Translation

```python
pipeline = DaxTranslationPipeline(schema_columns=['AMOUNT', 'UNITS'])
pipeline.load_measures_from_dax(dax_text)
pipeline.add_column_mappings({'Amount': 'AMOUNT', 'Units': 'UNITS'})
results = pipeline.resolve_all()

for measure, sql in results.items():
    print(f"{measure} → {sql}")
```

### Pattern 2: Error Handling

```python
pipeline = DaxTranslationPipeline(schema_columns=['AMOUNT'])
is_valid, errors = pipeline.validate()

if not is_valid:
    for error in errors:
        logger.error(error)
    return

pipeline.load_measures_from_dax(dax_text)
results = pipeline.resolve_all()

# Handle failures
failed = pipeline.get_failed_measures()
for measure_name, reason in failed:
    if "RANKX" in reason:
        # Use LLM for advanced functions
        sql = llm_translate(measure_name)
    else:
        # Skip configuration errors
        logger.warning(f"Skipping {measure_name}: {reason}")
```

### Pattern 3: Performance Optimization

```python
# Build pipeline once
pipeline = DaxTranslationPipeline(schema_columns=big_schema)
pipeline.add_column_mappings(big_mapping)

# Load and resolve once
pipeline.load_measures_from_dax(all_dax)
pipeline.resolve_all()

# Query cache multiple times
for measure in query_list:
    sql = pipeline.translate(measure)
```

### Pattern 4: Debugging

```python
pipeline = DaxTranslationPipeline(schema_columns=schema)
pipeline.load_measures_from_dax(dax_text)

# Export for analysis
export = pipeline.export_measures()
print(json.dumps(export, indent=2))

# Get statistics
stats = pipeline.get_statistics()
logger.info(f"Success rate: {stats['success_rate']}")

# Identify problems
failed = pipeline.get_failed_measures()
for measure, reason in failed:
    print(f"⚠️  {measure}: {reason}")
```

---

## Environment Variables

No environment variables required. Configuration via Python API only.

---

## Version History

- **v1.0.0**: Initial release
  - DAX parsing
  - SQL generation
  - Dependency resolution
  - 90+ functions supported

---

## Troubleshooting

### "Measure not found"

**Cause**: Measure name doesn't match DAX definition

**Solution**:
```python
measures = pipeline.dictionary.measures
print(f"Available: {list(measures.keys())}")
```

### "Column not in schema"

**Cause**: Schema columns not configured

**Solution**:
```python
pipeline.set_schema_columns(schema_columns)
# Then resolve again
```

### "Column not mapped"

**Cause**: Column mapping missing

**Solution**:
```python
pipeline.add_column_mapping(dax_col, schema_col)
# Then resolve again
```

### Low success rate

**Cause**: Configuration issues

**Solution**:
```python
is_valid, errors = pipeline.validate()
print(f"Errors: {errors}")

failed = pipeline.get_failed_measures()
for measure, reason in failed:
    print(f"{measure}: {reason}")
```

---

See also:
- [Integration Guide](./DAX_PIPELINE_INTEGRATION.md)
- [Architecture](./ARCHITECTURE.md)
- [Examples](../examples/dax_pipeline_example.py)
