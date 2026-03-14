# Formats System - Requirements

**Feature:** Platform-Specific Format Validation and Conversion
**Status:** In Progress
**Priority:** High
**Assigned:** January 21, 2026

---

## 1. Overview

The Formats System provides platform-specific naming conventions, identifier validation, and metadata value formatting for all supported connectors (Snowflake, Fabric/Power BI, Databricks). This ensures that semantic models can be correctly translated between platforms with proper syntax and data type handling.

---

## 2. User Stories

### US-FMT-001: Identifier Validation
**As a** data engineer  
**I want** the system to automatically validate and sanitize table/column names  
**So that** I don't encounter deployment failures due to invalid identifiers

**Acceptance Criteria:**
- System validates identifiers against platform-specific rules (regex, reserved keywords)
- Invalid identifiers are automatically sanitized (e.g., spaces → underscores)
- Validation errors provide clear feedback on what needs to be fixed
- Sanitization is reversible and documented

### US-FMT-002: Data Type Mapping
**As a** semantic model developer  
**I want** data types to be automatically mapped between platforms  
**So that** I don't have to manually translate type definitions

**Acceptance Criteria:**
- SML standard types (String, Integer, Float, Date, Boolean) map to platform-specific types
- Type mappings are bidirectional (source → SML → target)
- Unsupported types are flagged with clear error messages
- Type precision/scale is preserved where possible

### US-FMT-003: Metadata Value Formatting
**As a** BI analyst  
**I want** date literals and default values to be correctly formatted for each platform  
**So that** filters and calculations work correctly after sync

**Acceptance Criteria:**
- Date literals are formatted correctly (e.g., '2023-01-01'::DATE for Snowflake)
- Boolean values are converted to platform syntax (TRUE vs true vs 1)
- String literals are properly escaped
- Numeric formats preserve precision

### US-FMT-004: Extensible Format Registry
**As a** platform engineer  
**I want** to easily add support for new platforms  
**So that** the system can grow with our needs

**Acceptance Criteria:**
- New formats can be added by implementing a base interface
- Format definitions are self-contained and testable
- Registry automatically discovers available formats
- Documentation template exists for new formats

---

## 3. Functional Requirements

### REQ-FMT-001: Connector Specificity
- Every supported connector (Snowflake, Databricks, PowerBI/Fabric) MUST have a dedicated Format Definition
- Format definitions MUST be the single source of truth for syntax rules
- Format definitions MUST support DDL, JSON, and TMSL generation

### REQ-FMT-002: Identifier Validation & Sanitization
- System MUST provide `is_valid_identifier(name)` function using regex and keyword lists
- System MUST provide `sanitize_identifier(name)` function for automatic fixes
- Sanitization MUST handle:
  - Space conversion (spaces → underscores for Snowflake)
  - Case handling (preserve/convert based on platform)
  - Special character removal/replacement
  - Reserved keyword escaping

### REQ-FMT-003: Metadata Value Formatting
- System MUST format date literals correctly for each platform:
  - Snowflake: `'2023-01-01'::DATE`
  - Power BI (DAX): `DATE(2023, 1, 1)`
  - Databricks: `DATE '2023-01-01'`
- System MUST format boolean literals correctly:
  - SQL: `TRUE` / `FALSE`
  - DAX: `TRUE()` / `FALSE()`
  - Python: `True` / `False`
- System MUST provide `is_valid_type()` and `sanitize_type()` for metadata values

### REQ-FMT-004: Type Mapping Registry
- Each format MUST provide mapping from SML Standard Types to System Specific Types
- Mappings MUST be bidirectional
- Unsupported type combinations MUST raise clear errors

### REQ-FMT-005: Extensibility
- Format system MUST use plugin architecture
- New formats MUST be addable without modifying core code
- Format definitions MUST be independently testable

---

## 4. Non-Functional Requirements

### NFR-FMT-001: Performance
- Identifier validation MUST complete in < 1ms per identifier
- Type mapping lookups MUST be O(1) complexity
- Format registry initialization MUST complete in < 100ms

### NFR-FMT-002: Maintainability
- Each format definition MUST be in a separate file
- Format definitions MUST have 100% test coverage
- Format rules MUST be documented with examples

### NFR-FMT-003: Error Handling
- Validation errors MUST include the invalid value and reason
- Sanitization MUST log all transformations
- Unsupported operations MUST fail fast with clear messages

---

## 5. Constraints

- Format definitions MUST NOT depend on each other
- Format validation MUST be stateless
- Format system MUST work offline (no external API calls)

---

## 6. Assumptions

- Platform syntax rules are stable and well-documented
- Reserved keyword lists are maintained by platform vendors
- Type mappings may be lossy (e.g., VARIANT → STRING)

---

## 7. Dependencies

- Pydantic for validation models
- Python `re` module for regex validation
- SML/OSI intermediate models

---

## 8. Open Questions

1. How should we handle platform-specific types with no SML equivalent?
2. Should sanitization be configurable (strict vs lenient)?
3. How do we version format definitions when platforms update?
4. Should we support custom format extensions for enterprise needs?

---

## 9. Out of Scope

- Runtime type conversion (handled by connectors)
- Data validation (handled by source systems)
- Schema evolution tracking
- Format migration tools
