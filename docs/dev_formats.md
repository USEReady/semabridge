# Formats folder - convert fabric to supported structure in SF

Status: In Progress
Priority: High
Time: Time-intensive
assigned on : January 21, 2026

Formats folder: 

for each and every source and target connector supported by semabridge, store a list of the naming conventions and rules followed by that particular connector, so when a sync is done from a source to target, the necessary rules can be referred to, implemented and followed in code. 

include regex like checks to validate the name format and conversion functions to make a particular erroneous input abide by the rules. please give documentation for this too. 

Make sure that this includes metadata-specific formats for each connector (i.e snowflake, databricks, powerbi/fabric)

For eg Date format in one system might be different in another, so all this information needs to be cleaarly stored. Any other such variations should be captured along with their validation and conversion mechanism. Remember, this is only needed for the metadata layer, as we are doing a semantic sync.  

---

---

---

# Semabridge: Formats, Naming & Metadata Standards

**Scope:** Identifier validation, reserved keyword management, and **metadata value formatting** (dates, literals, types) for the semantic layer.

## 1. Core Requirements

- **REQ-FMT-001 (Connector Specificity):**
    - Every supported connector (Snowflake, Databricks, PowerBI/Fabric) must have a dedicated "Format Definition" in the `formats/` directory.
    - This definition serves as the single source of truth for all syntax rules required to generate valid metadata (DDL, JSON, TMSL) for that system.
- **REQ-FMT-002 (Identifier Validation & Sanitization):**
    - **Validation:** A function `is_valid_identifier(name)` using Regex and keyword lists.
    - **Sanitization:** A function `sanitize_identifier(name)` that automatically fixes non-compliant names (e.g., converting spaces to underscores for Snowflake, handling casing).
- **REQ-FMT-003 (Metadata Value Formatting):**
    - The module must define rules for formatting **literals and default values** found in semantic metadata.
    - *Example:* A semantic model defines a default filter on `OrderDate = '2023-01-01'`.
        - **Snowflake:** Requires `'2023-01-01'::DATE`.
        - **Power BI (DAX):** Requires `DATE(2023, 1, 1)`.
        - **Databricks:** Requires `DATE '2023-01-01'`.
    - The system must validate and convert these literals during the osi/sml `-> Target` phase.
    - The is_valid_type(); sanitize_type() must be available for metadata value literals as well.
- **REQ-FMT-004 (Type Mapping Registry):**
    - Each format must provide a mapping from **SML Standard Types** (String, Integer, Float, Date, Boolean) to the **System Specific Types** used in their metadata definitions.

---

## 2. Directory Structure

The structure now explicitly includes Databricks and separates Naming from Value formatting logic if complex. Make the structure and form of implementation extensible so more formats can be added easily with time. 

Plaintext

`semabridge/
├── src/
│   └── semabridge/
│       ├── formats/
│       │   ├── __init__.py           # Registry
│       │   ├── base_format.py        # Abstract Base Class
│       │   ├── snowflake_fmt.py      # Snowflake implementation
│       │   ├── fabric_fmt.py         # PowerBI/Fabric implementation
│       │   └── databricks_fmt.py     # Databricks implementation
│       └── ...`

---

## 3. Implementation Standards

### 3.1 The Expanded Interface (`base_format.py`)

This interface now handles both *Names* (Identifiers) and *Values* (Metadata Literals).

Python

`from abc import ABC, abstractmethod
from typing import List, Any
import re

class BaseFormatDefinition(ABC):
    """
    Abstract contract for system-specific syntax rules (Identifiers & Data).
    """
    
    # --- Section A: Naming & Identifiers ---
    
    @property
    @abstractmethod
    def max_identifier_length(self) -> int:
        pass

    @abstractmethod
    def is_valid_identifier(self, name: str) -> bool:
        """Returns True if the name meets regex/keyword rules."""
        pass

    @abstractmethod
    def sanitize_identifier(self, name: str) -> str:
        """Converts invalid name -> valid name (e.g. 'My Table' -> 'MY_TABLE')."""
        pass

    # --- Section B: Metadata Value Formatting ---

    @abstractmethod
    def format_date_literal(self, iso_date_str: str) -> str:
        """
        Converts generic 'YYYY-MM-DD' to system specific string.
        Input: '2023-12-31'
        Output (Snowflake): "'2023-12-31'"
        Output (Fabric): "DATE(2023, 12, 31)"
        """
        pass

    @abstractmethod
    def format_boolean_literal(self, value: bool) -> str:
        """
        Converts python bool to system string.
        Output (Python): True
        Output (SQL): 'TRUE' or '1'
        output (DAX): TRUE()
        """
        pass

    @abstractmethod
    def map_data_type(self, sml_type: str) -> str:
        """
        Maps SML types to Target Metadata types.
        Input: 'string' -> Output: 'VARCHAR(16777216)' (Snowflake)
        Input: 'string' -> Output: 'string' (Power BI)
        """
        pass`

### 3. Validation & Conversion Flow

---

When the `SmlToTargetConverter` processes a model, it must use these functions to transform **values** found in the metadata (like default values, partition keys, or security filters).

**Example Workflow:**

1. **Input (SML):** A measure has a description with a default filter: `date > 2023-01-01`.
2. **Lookup:** Converter calls `target_fmt.format_date_literal('2023-01-01')`.
3. **Transformation:**
    - If Target is **Databricks**: Result is `DATE '2023-01-01'`.
    - If Target is **Fabric**: Result is `DATE(2023, 1, 1)`.
4. **Code Generation:** The result is injected into the final Python code and the sync is completed successfully.

## 4. Developer Documentation: Adding a Format

1. **Create File:** `src/semabridge/formats/new_system_fmt.py`
2. **Define Regex:** Add the `VALID_PATTERN` for identifiers.
3. **Define Literals:** Implement `format_date_literal` and others. **Crucial:** Check how the target system accepts hardcoded values in its DDL/Scripting language.
4. **Define Types:** Map the 5 SML primitive types to the target's native types.
5. **Test:** Add unit tests in `tests/formats/test_new_system.py` passing dirty strings and checking sanitized outputs.
