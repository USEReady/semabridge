# Formats System - Implementation Tasks

**Feature:** Platform-Specific Format Validation and Conversion
**Status:** Not Started

---

## Task List

- [ ] 1. Core Format Infrastructure
  - [ ] 1.1 Create base format interface (`base_format.py`)
  - [ ] 1.2 Implement format registry with auto-discovery
  - [ ] 1.3 Add format error exception classes
  - [ ] 1.4 Create format registry initialization logic

- [ ] 2. Snowflake Format Implementation
  - [ ] 2.1 Implement Snowflake identifier validation
  - [ ] 2.2 Implement Snowflake identifier sanitization
  - [ ] 2.3 Add Snowflake reserved keywords list
  - [ ] 2.4 Implement Snowflake date literal formatting
  - [ ] 2.5 Implement Snowflake boolean literal formatting
  - [ ] 2.6 Implement Snowflake string literal formatting
  - [ ] 2.7 Create Snowflake type mapping (SML → Snowflake)
  - [ ] 2.8 Create Snowflake reverse type mapping (Snowflake → SML)

- [ ] 3. Fabric/Power BI Format Implementation
  - [ ] 3.1 Implement Fabric identifier validation
  - [ ] 3.2 Implement Fabric identifier sanitization
  - [ ] 3.3 Add DAX reserved keywords list
  - [ ] 3.4 Implement DAX date literal formatting
  - [ ] 3.5 Implement DAX boolean literal formatting
  - [ ] 3.6 Implement DAX string literal formatting
  - [ ] 3.7 Create TMSL type mapping (SML → TMSL)
  - [ ] 3.8 Create TMSL reverse type mapping (TMSL → SML)

- [ ] 4. Databricks Format Implementation (Optional)
  - [ ] 4.1 Implement Databricks identifier validation
  - [ ] 4.2 Implement Databricks identifier sanitization
  - [ ] 4.3 Add Databricks reserved keywords list
  - [ ] 4.4 Implement Databricks date literal formatting
  - [ ] 4.5 Implement Databricks type mappings

- [ ] 5. Testing
  - [ ] 5.1 Write unit tests for base format interface
  - [ ] 5.2 Write unit tests for Snowflake format (identifier validation)
  - [ ] 5.3 Write unit tests for Snowflake format (sanitization)
  - [ ] 5.4 Write unit tests for Snowflake format (literal formatting)
  - [ ] 5.5 Write unit tests for Snowflake format (type mapping)
  - [ ] 5.6 Write unit tests for Fabric format (all features)
  - [ ] 5.7 Write integration tests with converters
  - [ ] 5.8 Add edge case tests (empty strings, special characters, etc.)

- [ ] 6. Integration with Converters
  - [ ] 6.1 Update SML → Snowflake converter to use format definitions
  - [ ] 6.2 Update SML → Fabric converter to use format definitions
  - [ ] 6.3 Update OSI → SML converter to use format definitions
  - [ ] 6.4 Add format validation to pre-flight checks

- [ ] 7. Documentation
  - [ ] 7.1 Write developer guide for adding new formats
  - [ ] 7.2 Document format registry usage
  - [ ] 7.3 Create format comparison matrix
  - [ ] 7.4 Add inline code documentation
  - [ ] 7.5 Update architecture documentation

- [ ] 8. Performance Optimization
  - [ ] 8.1 Profile format validation performance
  - [ ] 8.2 Optimize regex patterns
  - [ ] 8.3 Add caching for repeated validations
  - [ ] 8.4 Benchmark type mapping lookups

---

## Task Details

### 1.1 Create base format interface
**Description:** Implement the abstract base class that all format definitions must inherit from.

**Files:**
- `src/semabridge/formats/base_format.py`

**Acceptance Criteria:**
- All abstract methods are defined
- Docstrings include examples
- Type hints are complete
- Helper methods are implemented

---

### 2.1 Implement Snowflake identifier validation
**Description:** Create regex-based validation for Snowflake identifiers.

**Files:**
- `src/semabridge/formats/snowflake_fmt.py`

**Acceptance Criteria:**
- Validates unquoted identifiers correctly
- Checks against reserved keywords
- Enforces max length (255 characters)
- Returns clear boolean result

---

### 5.2 Write unit tests for Snowflake format
**Description:** Comprehensive test coverage for Snowflake format implementation.

**Files:**
- `tests/formats/test_snowflake_fmt.py`

**Test Cases:**
- Valid identifiers (alphanumeric, underscores)
- Invalid identifiers (spaces, special chars, starting with number)
- Reserved keywords
- Sanitization transformations
- Edge cases (empty string, very long names)

**Acceptance Criteria:**
- 100% code coverage for Snowflake format
- All edge cases covered
- Tests are independent and repeatable

---

### 6.1 Update SML → Snowflake converter
**Description:** Integrate format definitions into the existing converter.

**Files:**
- `src/semabridge/converter/sml_to_snowflake.py`

**Changes:**
- Import format registry
- Use `sanitize_identifier()` for all names
- Use `map_data_type()` for type conversion
- Use literal formatters for default values

**Acceptance Criteria:**
- All identifiers are validated
- Type mappings use format definitions
- Existing tests still pass
- New tests verify format integration

---

## Dependencies

- Task 1.1 must be completed before 2.1, 3.1, 4.1
- Task 1.2 must be completed before 6.x
- Tasks 2.x must be completed before 5.2
- Tasks 6.x depend on completion of 2.x and 3.x

---

## Estimated Effort

| Task Group | Estimated Hours |
|------------|----------------|
| 1. Core Infrastructure | 8 hours |
| 2. Snowflake Format | 12 hours |
| 3. Fabric Format | 12 hours |
| 4. Databricks Format | 10 hours (optional) |
| 5. Testing | 16 hours |
| 6. Integration | 12 hours |
| 7. Documentation | 8 hours |
| 8. Performance | 6 hours |
| **Total** | **84 hours** (74 without Databricks) |

---

## Success Criteria

- [ ] All format implementations pass 100% of unit tests
- [ ] Integration tests with converters pass
- [ ] No performance regression in conversion pipeline
- [ ] Documentation is complete and accurate
- [ ] Code review approved by 2+ team members
