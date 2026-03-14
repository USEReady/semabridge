# Formats System - Design

**Feature:** Platform-Specific Format Validation and Conversion
**Version:** 1.0
**Last Updated:** February 6, 2026

---

## 1. Architecture Overview

The Formats System provides a plugin-based architecture for platform-specific syntax rules, identifier validation, and metadata value formatting.

```
┌─────────────────────────────────────────────────────────┐
│                   Format Registry                        │
│  (Auto-discovers and registers format definitions)      │
└────────────────┬────────────────────────────────────────┘
                 │
        ┌────────┴────────┬────────────┬──────────────┐
        │                 │            │              │
   ┌────▼─────┐    ┌─────▼────┐  ┌───▼──────┐  ┌───▼──────┐
   │Snowflake │    │  Fabric  │  │Databricks│  │  Future  │
   │  Format  │    │  Format  │  │  Format  │  │  Formats │
   └──────────┘    └──────────┘  └──────────┘  └──────────┘
        │                 │            │
        └─────────┬───────┴────────────┘
                  │
         ┌────────▼────────┐
         │  Base Format    │
         │   Interface     │
         └─────────────────┘
```

---

## 2. Component Design

### 2.1 Base Format Interface

**File:** `src/semabridge/formats/base_format.py`

```python
from abc import ABC, abstractmethod
from typing import List, Dict, Any
import re

class BaseFormatDefinition(ABC):
    """
    Abstract contract for system-specific syntax rules.
    
    All format implementations must inherit from this class
    and implement all abstract methods.
    """
    
    # ===== Section A: Naming & Identifiers =====
    
    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Return the platform name (e.g., 'snowflake', 'fabric')."""
        pass
    
    @property
    @abstractmethod
    def max_identifier_length(self) -> int:
        """Maximum allowed length for identifiers."""
        pass
    
    @property
    @abstractmethod
    def reserved_keywords(self) -> List[str]:
        """List of reserved keywords that cannot be used as identifiers."""
        pass
    
    @abstractmethod
    def is_valid_identifier(self, name: str) -> bool:
        """
        Check if identifier meets platform rules.
        
        Args:
            name: Identifier to validate
            
        Returns:
            True if valid, False otherwise
        """
        pass
    
    @abstractmethod
    def sanitize_identifier(self, name: str) -> str:
        """
        Convert invalid identifier to valid one.
        
        Args:
            name: Identifier to sanitize
            
        Returns:
            Sanitized identifier
            
        Example:
            'My Table' -> 'MY_TABLE' (Snowflake)
            'My Table' -> 'My Table' (Fabric - spaces allowed)
        """
        pass
    
    # ===== Section B: Metadata Value Formatting =====
    
    @abstractmethod
    def format_date_literal(self, iso_date_str: str) -> str:
        """
        Convert ISO date string to platform-specific literal.
        
        Args:
            iso_date_str: Date in 'YYYY-MM-DD' format
            
        Returns:
            Platform-specific date literal
            
        Examples:
            Snowflake: '2023-12-31' -> "'2023-12-31'::DATE"
            Fabric: '2023-12-31' -> "DATE(2023, 12, 31)"
            Databricks: '2023-12-31' -> "DATE '2023-12-31'"
        """
        pass
    
    @abstractmethod
    def format_boolean_literal(self, value: bool) -> str:
        """
        Convert Python bool to platform-specific literal.
        
        Args:
            value: Boolean value
            
        Returns:
            Platform-specific boolean literal
            
        Examples:
            SQL: True -> 'TRUE'
            DAX: True -> 'TRUE()'
        """
        pass
    
    @abstractmethod
    def format_string_literal(self, value: str) -> str:
        """
        Escape and format string literal.
        
        Args:
            value: String value
            
        Returns:
            Properly escaped string literal
        """
        pass
    
    # ===== Section C: Type Mapping =====
    
    @abstractmethod
    def map_data_type(self, sml_type: str) -> str:
        """
        Map SML standard type to platform-specific type.
        
        Args:
            sml_type: SML type (string, integer, float, date, boolean)
            
        Returns:
            Platform-specific type
            
        Examples:
            Snowflake: 'string' -> 'VARCHAR(16777216)'
            Fabric: 'string' -> 'string'
            Databricks: 'string' -> 'STRING'
        """
        pass
    
    @abstractmethod
    def reverse_map_data_type(self, platform_type: str) -> str:
        """
        Map platform-specific type back to SML standard type.
        
        Args:
            platform_type: Platform-specific type
            
        Returns:
            SML standard type
        """
        pass
    
    # ===== Section D: Validation Helpers =====
    
    def validate_and_sanitize(self, name: str) -> tuple[bool, str, str]:
        """
        Validate identifier and return sanitized version.
        
        Args:
            name: Identifier to validate
            
        Returns:
            Tuple of (is_valid, sanitized_name, error_message)
        """
        if self.is_valid_identifier(name):
            return (True, name, "")
        
        sanitized = self.sanitize_identifier(name)
        if self.is_valid_identifier(sanitized):
            return (False, sanitized, f"Identifier '{name}' sanitized to '{sanitized}'")
        
        return (False, sanitized, f"Cannot sanitize identifier '{name}'")
```

### 2.2 Snowflake Format Implementation

**File:** `src/semabridge/formats/snowflake_fmt.py`

```python
import re
from typing import List
from .base_format import BaseFormatDefinition

class SnowflakeFormat(BaseFormatDefinition):
    """Snowflake-specific format rules."""
    
    # Snowflake identifier rules:
    # - Max 255 characters
    # - Unquoted: [A-Za-z_][A-Za-z0-9_$]*
    # - Quoted: Any character except double quote
    
    IDENTIFIER_PATTERN = re.compile(r'^[A-Za-z_][A-Za-z0-9_$]*$')
    
    RESERVED_KEYWORDS = [
        'SELECT', 'FROM', 'WHERE', 'INSERT', 'UPDATE', 'DELETE',
        'CREATE', 'DROP', 'ALTER', 'TABLE', 'VIEW', 'INDEX',
        'PRIMARY', 'KEY', 'FOREIGN', 'REFERENCES', 'CONSTRAINT',
        'AND', 'OR', 'NOT', 'NULL', 'IS', 'IN', 'BETWEEN',
        'LIKE', 'ORDER', 'BY', 'GROUP', 'HAVING', 'UNION',
        'JOIN', 'INNER', 'LEFT', 'RIGHT', 'OUTER', 'ON',
        # Add more as needed
    ]
    
    @property
    def platform_name(self) -> str:
        return "snowflake"
    
    @property
    def max_identifier_length(self) -> int:
        return 255
    
    @property
    def reserved_keywords(self) -> List[str]:
        return self.RESERVED_KEYWORDS
    
    def is_valid_identifier(self, name: str) -> bool:
        """Check if identifier is valid for Snowflake."""
        if not name or len(name) > self.max_identifier_length:
            return False
        
        # Check if it's a reserved keyword
        if name.upper() in self.reserved_keywords:
            return False
        
        # Check pattern
        return bool(self.IDENTIFIER_PATTERN.match(name))
    
    def sanitize_identifier(self, name: str) -> str:
        """Sanitize identifier for Snowflake."""
        if not name:
            return "UNNAMED"
        
        # Convert to uppercase (Snowflake convention)
        sanitized = name.upper()
        
        # Replace spaces and hyphens with underscores
        sanitized = re.sub(r'[\s\-]+', '_', sanitized)
        
        # Remove invalid characters
        sanitized = re.sub(r'[^A-Z0-9_$]', '', sanitized)
        
        # Ensure starts with letter or underscore
        if sanitized and not sanitized[0].isalpha() and sanitized[0] != '_':
            sanitized = '_' + sanitized
        
        # Truncate if too long
        if len(sanitized) > self.max_identifier_length:
            sanitized = sanitized[:self.max_identifier_length]
        
        # Handle reserved keywords
        if sanitized in self.reserved_keywords:
            sanitized = f"{sanitized}_COL"
        
        return sanitized or "UNNAMED"
    
    def format_date_literal(self, iso_date_str: str) -> str:
        """Format date for Snowflake."""
        return f"'{iso_date_str}'::DATE"
    
    def format_boolean_literal(self, value: bool) -> str:
        """Format boolean for Snowflake."""
        return 'TRUE' if value else 'FALSE'
    
    def format_string_literal(self, value: str) -> str:
        """Format string for Snowflake."""
        # Escape single quotes
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    
    def map_data_type(self, sml_type: str) -> str:
        """Map SML type to Snowflake type."""
        mapping = {
            'string': 'VARCHAR(16777216)',
            'integer': 'NUMBER(38,0)',
            'float': 'FLOAT',
            'decimal': 'NUMBER(38,10)',
            'boolean': 'BOOLEAN',
            'date': 'DATE',
            'datetime': 'TIMESTAMP_NTZ',
            'time': 'TIME',
            'binary': 'BINARY',
            'variant': 'VARIANT',
        }
        return mapping.get(sml_type.lower(), 'VARCHAR(16777216)')
    
    def reverse_map_data_type(self, platform_type: str) -> str:
        """Map Snowflake type to SML type."""
        platform_upper = platform_type.upper().split('(')[0].strip()
        
        mapping = {
            'VARCHAR': 'string',
            'CHAR': 'string',
            'STRING': 'string',
            'TEXT': 'string',
            'NUMBER': 'decimal',
            'NUMERIC': 'decimal',
            'DECIMAL': 'decimal',
            'INT': 'integer',
            'INTEGER': 'integer',
            'BIGINT': 'integer',
            'FLOAT': 'float',
            'DOUBLE': 'float',
            'BOOLEAN': 'boolean',
            'DATE': 'date',
            'TIMESTAMP': 'datetime',
            'TIMESTAMP_NTZ': 'datetime',
            'TIMESTAMP_LTZ': 'datetime',
            'TIMESTAMP_TZ': 'datetime',
            'TIME': 'time',
            'BINARY': 'binary',
            'VARIANT': 'variant',
            'OBJECT': 'variant',
            'ARRAY': 'variant',
        }
        return mapping.get(platform_upper, 'string')
```

### 2.3 Fabric/Power BI Format Implementation

**File:** `src/semabridge/formats/fabric_fmt.py`

```python
import re
from typing import List
from .base_format import BaseFormatDefinition

class FabricFormat(BaseFormatDefinition):
    """Microsoft Fabric / Power BI format rules."""
    
    # Fabric is more lenient with identifiers
    # Spaces are allowed, special characters are allowed
    
    @property
    def platform_name(self) -> str:
        return "fabric"
    
    @property
    def max_identifier_length(self) -> int:
        return 128  # Power BI limit
    
    @property
    def reserved_keywords(self) -> List[str]:
        # DAX reserved words
        return [
            'TRUE', 'FALSE', 'VAR', 'RETURN', 'IF', 'SWITCH',
            'AND', 'OR', 'NOT', 'IN', 'BLANK',
        ]
    
    def is_valid_identifier(self, name: str) -> bool:
        """Check if identifier is valid for Fabric."""
        if not name or len(name) > self.max_identifier_length:
            return False
        
        # Fabric allows most characters including spaces
        # Just check for control characters
        return not any(ord(c) < 32 for c in name)
    
    def sanitize_identifier(self, name: str) -> str:
        """Sanitize identifier for Fabric."""
        if not name:
            return "Unnamed"
        
        # Remove control characters
        sanitized = ''.join(c for c in name if ord(c) >= 32)
        
        # Truncate if too long
        if len(sanitized) > self.max_identifier_length:
            sanitized = sanitized[:self.max_identifier_length]
        
        return sanitized or "Unnamed"
    
    def format_date_literal(self, iso_date_str: str) -> str:
        """Format date for DAX."""
        # Parse YYYY-MM-DD
        parts = iso_date_str.split('-')
        if len(parts) == 3:
            year, month, day = parts
            return f"DATE({year}, {month}, {day})"
        return f"DATE(2000, 1, 1)"  # Fallback
    
    def format_boolean_literal(self, value: bool) -> str:
        """Format boolean for DAX."""
        return 'TRUE()' if value else 'FALSE()'
    
    def format_string_literal(self, value: str) -> str:
        """Format string for DAX."""
        # Escape double quotes
        escaped = value.replace('"', '""')
        return f'"{escaped}"'
    
    def map_data_type(self, sml_type: str) -> str:
        """Map SML type to TMSL type."""
        mapping = {
            'string': 'string',
            'integer': 'int64',
            'float': 'double',
            'decimal': 'decimal',
            'boolean': 'boolean',
            'date': 'dateTime',
            'datetime': 'dateTime',
            'time': 'dateTime',
            'binary': 'binary',
            'variant': 'string',
        }
        return mapping.get(sml_type.lower(), 'string')
    
    def reverse_map_data_type(self, platform_type: str) -> str:
        """Map TMSL type to SML type."""
        platform_lower = platform_type.lower()
        
        mapping = {
            'string': 'string',
            'int64': 'integer',
            'double': 'float',
            'decimal': 'decimal',
            'boolean': 'boolean',
            'datetime': 'datetime',
            'binary': 'binary',
        }
        return mapping.get(platform_lower, 'string')
```

### 2.4 Format Registry

**File:** `src/semabridge/formats/__init__.py`

```python
from typing import Dict, Type
from .base_format import BaseFormatDefinition
from .snowflake_fmt import SnowflakeFormat
from .fabric_fmt import FabricFormat

class FormatRegistry:
    """
    Central registry for format definitions.
    
    Automatically discovers and registers format implementations.
    """
    
    _formats: Dict[str, BaseFormatDefinition] = {}
    
    @classmethod
    def register(cls, format_def: BaseFormatDefinition) -> None:
        """Register a format definition."""
        cls._formats[format_def.platform_name] = format_def
    
    @classmethod
    def get(cls, platform_name: str) -> BaseFormatDefinition:
        """Get format definition by platform name."""
        if platform_name not in cls._formats:
            raise ValueError(f"Unknown platform: {platform_name}")
        return cls._formats[platform_name]
    
    @classmethod
    def list_platforms(cls) -> list[str]:
        """List all registered platforms."""
        return list(cls._formats.keys())
    
    @classmethod
    def initialize(cls) -> None:
        """Initialize registry with built-in formats."""
        cls.register(SnowflakeFormat())
        cls.register(FabricFormat())

# Auto-initialize on import
FormatRegistry.initialize()

# Convenience exports
def get_format(platform: str) -> BaseFormatDefinition:
    """Get format definition for platform."""
    return FormatRegistry.get(platform)

__all__ = [
    'BaseFormatDefinition',
    'FormatRegistry',
    'get_format',
    'SnowflakeFormat',
    'FabricFormat',
]
```

---

## 3. Integration Points

### 3.1 Converter Integration

Converters use format definitions during transformation:

```python
from semabridge.formats import get_format

class SmlToSnowflakeConverter:
    def __init__(self):
        self.format = get_format('snowflake')
    
    def convert_column(self, sml_column):
        # Sanitize name
        name = self.format.sanitize_identifier(sml_column.unique_name)
        
        # Map type
        sf_type = self.format.map_data_type(sml_column.data_type)
        
        return f"{name} {sf_type}"
```

### 3.2 Validation Integration

Validators use format definitions for pre-flight checks:

```python
from semabridge.formats import get_format

def validate_model_for_target(sml_model, target_platform):
    format_def = get_format(target_platform)
    errors = []
    
    for dataset in sml_model.datasets:
        is_valid, sanitized, msg = format_def.validate_and_sanitize(dataset.unique_name)
        if not is_valid:
            errors.append(f"Dataset: {msg}")
    
    return errors
```

---

## 4. Testing Strategy

### 4.1 Unit Tests

Each format implementation must have comprehensive unit tests:

```python
# tests/formats/test_snowflake_fmt.py
import pytest
from semabridge.formats import SnowflakeFormat

def test_valid_identifier():
    fmt = SnowflakeFormat()
    assert fmt.is_valid_identifier("VALID_NAME")
    assert fmt.is_valid_identifier("Table_123")
    assert not fmt.is_valid_identifier("123_Table")
    assert not fmt.is_valid_identifier("SELECT")  # Reserved

def test_sanitize_identifier():
    fmt = SnowflakeFormat()
    assert fmt.sanitize_identifier("My Table") == "MY_TABLE"
    assert fmt.sanitize_identifier("Product-Name") == "PRODUCT_NAME"
    assert fmt.sanitize_identifier("123abc") == "_123ABC"

def test_date_formatting():
    fmt = SnowflakeFormat()
    assert fmt.format_date_literal("2023-12-31") == "'2023-12-31'::DATE"

def test_type_mapping():
    fmt = SnowflakeFormat()
    assert fmt.map_data_type("string") == "VARCHAR(16777216)"
    assert fmt.map_data_type("integer") == "NUMBER(38,0)"
```

### 4.2 Integration Tests

Test format definitions with real converters:

```python
def test_snowflake_conversion_with_format():
    sml_model = create_test_sml_model()
    converter = SmlToSnowflakeConverter()
    
    ddl = converter.convert(sml_model)
    
    # Verify identifiers are valid
    assert "MY_TABLE" in ddl
    assert "CUSTOMER_ID" in ddl
```

---

## 5. Performance Considerations

- Format registry is initialized once at startup
- Identifier validation uses compiled regex patterns
- Type mappings use dictionary lookups (O(1))
- No external API calls or I/O operations

---

## 6. Error Handling

```python
class FormatError(Exception):
    """Base exception for format-related errors."""
    pass

class InvalidIdentifierError(FormatError):
    """Raised when identifier cannot be sanitized."""
    pass

class UnsupportedTypeError(FormatError):
    """Raised when type mapping is not available."""
    pass
```

---

## 7. Future Enhancements

1. **Custom Format Extensions**: Allow users to define custom formats
2. **Format Versioning**: Support multiple versions of platform formats
3. **Format Migration Tools**: Assist in migrating between format versions
4. **Performance Profiling**: Add metrics for format operations
5. **Databricks Support**: Add Databricks format implementation
