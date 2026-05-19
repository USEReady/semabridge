# Fabric Semantic Logic Preservation & Snowflake Semantic Execution
## Technical Scope & Design Document

**Version:** 1.0  
**Date:** May 11, 2026  
**Status:** Design Proposal  
**Audience:** Architecture Review, Engineering Leadership, Product

---

## Executive Summary

This document defines the technical vision and implementation strategy for enabling **semantic-equivalent execution** of Microsoft Fabric semantic models in Snowflake. The goal is not to port Fabric's BI engine, but rather to ensure that analytical queries executing against a Snowflake semantic layer produce results that are semantically equivalent to those produced in Fabric, accounting for platform-specific differences.

**Key Success Criteria:**
- ✅ **Semantic Parity:** 99%+ of Fabric measures execute in Snowflake with <0.1% result deviation
- ✅ **DAX Coverage:** Support Tier 1-4 DAX patterns deterministically; Tier 5 via managed fallback
- ✅ **Query Logic Preservation:** Power Query transformations translated to Snowflake SQL
- ✅ **Context Fidelity:** Filter context, row context, and relationship behavior preserved
- ✅ **Performance:** Comparable query performance to native Snowflake queries
- ✅ **Reliability:** <1% semantic translation failures, zero silent errors

**Estimated Scope:** 6-8 months, team of 4-5 engineers

---

## 1. Initiative Overview

### 1.1 Business Objective

**Problem Statement:**
Organizations using Microsoft Fabric for semantic modeling face a portability problem: their carefully crafted DAX measures, time intelligence logic, and analytical calculations are locked into Fabric's BI engine. When they want to leverage Snowflake for data warehousing and analytics, they must manually recreate this logic in SQL—a time-consuming, error-prone process that leads to:

- **Inconsistency:** Different results between Fabric and Snowflake
- **Maintenance Burden:** Dual maintenance of business logic
- **Migration Friction:** High cost to switch platforms
- **Lock-in Risk:** Trapped by analytical complexity

**Solution:**
Semabridge will provide a **semantic translation layer** that automatically converts Fabric semantic definitions into Snowflake-native semantic assets while preserving analytical behavior. This enables:

- **Platform Independence:** Write logic once in Fabric, run identically in Snowflake
- **Data Warehouse Consolidation:** Unified analytical definitions across platforms
- **Migration Path:** Clear, automated path from Fabric to Snowflake
- **Hybrid Architecture:** Run same semantic model in both Fabric and Snowflake

### 1.2 Technical Objective

To build a **deterministic semantic translation engine** that converts Fabric semantic models (DAX measures, M queries, relationships, hierarchies, time intelligence) into Snowflake semantic views that produce analytically equivalent results.

### 1.3 The Semantic Portability Problem

**Why This Is Hard:**

1. **Different Execution Models:**
   - Fabric: Column-oriented in-memory engine with implicit evaluation context
   - Snowflake: Row-oriented SQL engine with explicit query context
   - Gap: Requires mental model transformation

2. **DAX vs SQL Semantics:**
   - DAX has implicit **filter context** (what measures do you aggregate over?)
   - SQL requires explicit WHERE clauses and GROUP BY
   - DAX has implicit **row context** (which row am I currently evaluating?)
   - SQL has no equivalent concept

3. **Aggregation Ambiguity:**
   - DAX: `SUM([Amount])` means "sum over current context"
   - SQL: `SUM(amount)` is ambiguous without specifying grouping
   - Translation requires understanding semantic intent

4. **Time Intelligence Complexity:**
   - DAX: `TOTALYTD(SUM([Sales]), [Date])` handles date logic automatically
   - SQL: Requires explicit window functions, date arithmetic, calendar assumptions
   - Different calendar systems, fiscal years, holiday calendars

5. **Relationship Semantics:**
   - DAX: Relationships are declarative; DAX engine handles join logic
   - SQL: Relationships must be explicit in FROM/JOIN clauses
   - Complex relationships, circular references require special handling

### 1.4 Why DAX/M Query Parity Matters

**DAX Parity:**
- Measures represent months/years of analytical refinement
- Complex CALCULATE logic captures business rules
- Time intelligence implements fiscal/calendar logic
- Losing this in translation means losing competitive advantage
- Users must trust translation to adopt Snowflake

**M Query Parity:**
- Power Query transformations represent data preparation logic
- Query folding affects performance; translation must preserve it
- Custom functions represent domain logic
- Parameter-driven queries support dynamic filtering
- Losing transformations means replicating ETL in Snowflake

---

## 2. Current State Summary

### 2.1 Existing Strengths

| Component | Maturity | Strength |
|-----------|----------|----------|
| **OSI Intermediate Format** | Production | Vendor-neutral representation enables platform-agnostic translation |
| **Fabric Extraction** | Production | Reliable TMSL parsing, measure DAX extraction |
| **Snowflake Emission** | Production | Dynamic table generation, relationship preservation |
| **DAX Tiers 1-3** | Production | 85-90% of real-world measures translate deterministically |
| **Relationship Handling** | Production | Multi-column relationships, cardinality, cross-filter |
| **Validation Pipeline** | Production | 6-tier pre-deployment validation prevents errors |
| **Dependency Resolution** | Partial | Measure-to-measure dependencies resolved |
| **Time Intelligence** | Partial | TOTALYTD, TOTALMTD via window functions |

### 2.2 Architectural Strengths

1. **Modular Pipeline Design**
   - Extract → Convert → Validate → Transform → Emit
   - Each stage decoupled, testable independently
   - Easy to insert new translation logic

2. **AST-Based DAX Parser**
   - Recursive-descent parser generates Abstract Syntax Tree
   - Enables semantic analysis beyond regex patterns
   - Foundation for complex DAX translation

3. **Multi-Tier Translation**
   - Deterministic Tiers 1-4 for most common patterns
   - LLM fallback for edge cases (managed, not magical)
   - Clear failure modes and logging

4. **Comprehensive Validation**
   - Pre-deployment checks prevent data loss
   - Schema compatibility validation
   - Identifier grounding, PK integrity checks

### 2.3 Current Architectural Limitations

**Problem 1: No Semantic Context Model**
- Current system translates DAX → SQL without understanding analytical intent
- No representation of "what does this measure mean?"
- Cannot reason about semantic equivalence
- Impact: Cannot validate parity or detect semantic drift

**Problem 2: Limited Row Context Support**
- DAX row context (EARLIER, RANKX, iterators) has no SQL equivalent
- Current workaround: fall back to LLM
- Unreliable, expensive, not reproducible
- Impact: Cannot handle complex measures deterministically

**Problem 3: No Filter Context Propagation**
- Relationships are materialized in views (lost at query time)
- Cannot reason about how filters propagate through relationships
- Complex FILTER/CALCULATE patterns fail
- Impact: Measure results differ from Fabric

**Problem 4: Missing Time Intelligence Inference**
- System assumes date columns follow standard calendar
- Cannot detect fiscal calendars or custom date hierarchies
- YTD calculations may use wrong date boundaries
- Impact: Time-based measures produce incorrect results

**Problem 5: Incomplete M Query Translation**
- M queries are extracted but not transformed
- Power Query logic lost in translation
- Users must manually recreate ETL in Snowflake
- Impact: Query folding benefits not realized; transformation logic duplicated

**Problem 6: No Metadata Dependency Graph**
- Measure dependencies tracked but not visualized
- Cannot detect all circular dependencies
- Cannot reason about measure ordering
- Impact: Deployment order incorrect; circular dependencies undetected

---

## 3. Problem Definition

### 3.1 Why DAX Semantics Are Difficult to Replicate in SQL

**The Fundamental Mismatch:**

| Aspect | DAX | SQL |
|--------|-----|-----|
| **Execution Model** | Declarative; context-driven | Imperative; query-driven |
| **Filter Semantics** | Implicit via relationships | Explicit via WHERE clauses |
| **Aggregation** | Over implicit context | Over explicit GROUP BY |
| **Row Processing** | Iterator-based loops | Set-based operations |
| **Nulls** | BLANK ≠ NULL | NULL is undefined |
| **Type Coercion** | Automatic, lenient | Explicit, strict |
| **Evaluation Order** | LTR with context stacks | Top-down execution plan |

**Example: Simple Measure That Fails**
```
DAX:  Total Sales = SUM([Amount])
      → Means: "Sum Amount over current filter context"

SQL:  SELECT SUM(Amount) FROM Orders
      → Missing: What is "current filter context"? 
      → Missing: Which table are we grouping by?
      → Missing: How do relationships affect this?

Correct SQL requires:
      SELECT dim.DIM_ID, SUM(amount)
      FROM orders o
      LEFT JOIN dim ON o.dim_id = dim.dim_id
      WHERE <implicit_filters>
      GROUP BY dim.DIM_ID
```

**Why Translation Fails:**
1. DAX says "I want sum over context"; SQL requires explicit context
2. Translator must infer: What is the implicit context?
3. Context depends on: which dimensions are being filtered, which relationships are active
4. This information is not in the DAX formula; it's in the query environment

**The Core Problem:**
DAX measures are **context-sensitive functions** but SQL queries are **context-free statements**.

### 3.2 Why M Query Transformations Matter

**Power Query Role in Analytics:**

In a Fabric semantic model, M queries are NOT optional ETL:
- They are the **query source layer** for semantic tables
- They contain: data extraction, transformation, filtering, parameter handling
- They implement: data preparation logic that affects analytical results
- They enable: query folding (pushing filters to source system)

**Example:**
```m
let
    Source = Snowflake.Databases(...),
    Raw = Source{[Name="RAW_SALES"]}[Data],
    Filtered = Table.SelectRows(Raw, each [Year] > 2020),
    Transformed = Table.TransformColumns(Filtered, 
        {{"Amount", Currency.From, type Currency}})
in
    Transformed
```

This M query does:
1. Connects to Snowflake
2. Filters to Year > 2020
3. Converts Amount to Currency type
4. Returns transformed table

**Why Translation Matters:**
- Without translation, Snowflake user sees raw table (includes pre-2020 data)
- Results differ: Fabric measures aggregate over filtered data; Snowflake over full table
- Users cannot trust Snowflake for accurate analytics

**The Problem:**
- M query language is Turing-complete (functions, loops, conditions)
- SQL is declarative and has no loop constructs
- Not all M logic can be translated to SQL
- Example: `let x = ... if condition then f(x) else g(x)` cannot translate directly

### 3.3 Why Context Transition Is Difficult

**DAX Context Stacking:**

DAX evaluates expressions within layers of context:

```
CALCULATE(
  SUM([Amount]),
  FILTER(
    ALL(Date),
    [Year] = 2024
  )
)
```

This expression uses **context transition**:
1. Start with current filter context (e.g., Region="USA")
2. FILTER modifies context: removes all Date filters, adds [Year]=2024
3. ALL removes all filters on Date table
4. SUM executes with modified context
5. Result depends on context path

**SQL Equivalent (Attempted):**
```sql
SELECT SUM(amount)
FROM orders o
JOIN date d ON o.date_id = d.date_id
WHERE 1=1  -- region="USA" from outer context?
  AND d.year = 2024  -- FILTER modified context
  -- AND d.date IS NOT NULL  -- ALL removed filters?
```

**The Problem:**
- SQL cannot represent nested context stacks
- SQL cannot express "remove all filters on table X except"
- Must flatten context into WHERE clause
- Loses semantic meaning of context operations

**Why It's Hard:**
1. DAX context is implicit (in the query environment)
2. SQL context is explicit (in WHERE clause)
3. Translation requires inferring context from query structure
4. Multiple valid translations possible; must choose correct one

### 3.4 Why Time Intelligence Creates Challenges

**The Time Intelligence Problem:**

DAX time functions depend on assumptions:
- `TOTALYTD(SUM([Sales]), [Date])` assumes:
  - [Date] is a date column
  - "Year" is calendar year (not fiscal year)
  - Date boundaries are well-defined
  - No gaps in date sequence

**Example: Fiscal Year Challenge**
```
DAX:  YTD Sales = TOTALYTD(SUM([Sales]), [Date])
      Assumes: Year begins Jan 1, ends Dec 31

Business Reality: Fiscal year begins Jul 1, ends Jun 30

Result: DAX calculates wrong YTD
       (user must use DATESYTD with custom calendar)
```

**SQL Window Function Translation:**
```sql
SUM(sales) OVER (
  PARTITION BY YEAR(date)
  ORDER BY date
  ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
)
```

This assumes calendar year. For fiscal year:
```sql
SUM(sales) OVER (
  PARTITION BY YEAR(DATE_TRUNC('YEAR', 
    DATEADD(MONTH, -6, date)))  -- Shift by 6 months for fiscal year
  ORDER BY date
  ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
)
```

**Challenges:**
1. Must infer fiscal year from calendar table (how?)
2. Must detect non-standard date hierarchies
3. Must validate date continuity (gaps break YTD)
4. Must handle multi-calendar scenarios

### 3.5 Why Row Context and Filter Context Are Important

**Row Context:**
- DAX operates in **row context** when iterating table rows
- Example: `SUMX(Orders, [Order Amount])` iterates each row
- Row context provides current row values for expressions
- SQL has no equivalent (set-based operations only)

**Filter Context:**
- DAX operates in **filter context** when aggregating
- Example: `SUM([Amount])` aggregates over filtered rows
- Filter context comes from: slicers, relationships, CALCULATE modifiers
- SQL filter context comes from WHERE clause

**Why They Matter:**
- Row context enables complex iterative logic (impossible in SQL)
- Filter context enables relationship-aware calculations
- Losing them means losing analytical capability

**The Portability Challenge:**
- Fabric automatically manages both contexts
- Snowflake requires explicit representation
- Translation must infer context from query structure
- Inference often ambiguous or impossible

---

## 4. Semantic Preservation Goals

### 4.1 Parity Definition

**Semantic parity** means: Given the same data input, a Fabric semantic model and equivalent Snowflake semantic model produce identical (or near-identical) results under identical filter conditions.

**Tolerance:** <0.1% result deviation (accounting for floating-point differences)

### 4.2 Measure Parity

**Goal:** All measures translate such that:
```
Fabric Result ≈ Snowflake Result (within floating-point tolerance)
```

**Scope:**
- ✅ Tier 1: Direct aggregations (SUM, COUNT, AVG, etc.)
- ✅ Tier 2: Simple arithmetic combinations ([M1] + [M2])
- ✅ Tier 3: Time intelligence (TOTALYTD, SAMEPERIODLASTYEAR)
- ✅ Tier 4: Complex CALCULATE/FILTER/ALL
- 🔄 Tier 5: Iterators (SUMX, AVERAGEX, RANKX)—LLM fallback with validation

**Out of Scope (Won't Achieve):**
- ❌ EARLIER/EARLIEST row context (too complex without DAX engine)
- ❌ Dynamic relationships (USERELATIONSHIP) - will defer to Phase 2

### 4.3 Aggregation Parity

**Aggregation Functions Must Produce Identical Results:**

| Function | Scope | Validation Strategy |
|----------|-------|-------------------|
| SUM | Numeric columns | Compare against Fabric |
| COUNT | Distinct counts | Compare distinct counts |
| AVERAGE | Numeric columns | Allow ±0.01 variance |
| MIN/MAX | All types | Exact match |
| DISTINCTCOUNT | Dimension values | Compare cardinality |
| COUNTBLANK | Null handling | Match null counts |

**Null Handling:**
- DAX BLANK must translate to SQL NULL
- Must handle NULL propagation correctly
- Some aggregations treat NULL differently (COUNT excludes NULL; COUNTA includes)

### 4.4 Relationship Behavior

**Goals:**
- ✅ Relationships materialize in semantic view joins
- ✅ Cardinality enforced (many-to-one, one-to-one)
- ✅ Cross-filter direction honored (single vs bidirectional)
- ✅ Circular references detected and handled
- ⚠️ Dynamic relationship switching (USERELATIONSHIP) - Phase 2

**Validation:**
- Row counts match before/after joins
- Referential integrity maintained
- Join order optimized for query performance

### 4.5 Filter Propagation

**Goals:**
- ✅ WHERE filters propagate through relationships
- ✅ CALCULATE filters override ambient context
- ✅ ALL/ALLEXCEPT correctly modify filter context
- ✅ Multiple filter conditions AND together

**Example:**
```
Fabric Query:
  Region = "USA" AND Year = 2024
  Measure: SUM([Sales])

Snowflake Must Produce:
  SELECT SUM(amount)
  FROM orders o
  JOIN region r ON o.region_id = r.region_id
  JOIN date d ON o.date_id = d.date_id
  WHERE r.region = "USA" AND d.year = 2024
```

### 4.6 Time Intelligence Parity

**Goals:**
- ✅ TOTALYTD produces same results as Fabric
- ✅ SAMEPERIODLASTYEAR produces same results as Fabric
- ✅ Custom calendars detected and handled
- ✅ Fiscal year calendars supported

**Challenges:**
- Must auto-detect calendar type (calendar vs fiscal year)
- Must validate date continuity
- Must handle multiple date hierarchies

### 4.7 Context Behavior Parity

**Filter Context:**
- ✅ Implicit context from outer query preserved
- ✅ CALCULATE context overrides inherited context
- ✅ FILTER and ALL modifiers produce expected result subsets

**Row Context:**
- ⚠️ Simple iterator patterns (SUMX, etc.) - Phase 1
- ❌ Complex row context (EARLIER, RANKX) - Phase 2+

### 4.8 Hierarchy Behavior Parity

**Goals:**
- ✅ Hierarchies flatten to dimension attributes
- ✅ Drill-down relationships preserved
- ✅ Level-based calculations work
- ✅ Parent-child hierarchies handled

---

## 5. Target Architecture

### 5.1 End-to-End Semantic Translation Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│ FABRIC SEMANTIC MODEL (TMSL)                                    │
│ ├─ Tables (dimensions, facts)                                   │
│ ├─ Measures (DAX expressions)                                   │
│ ├─ Relationships (cardinality, cross-filter)                    │
│ ├─ Hierarchies (multi-level drill paths)                        │
│ ├─ Calculated Columns (DAX)                                     │
│ └─ Metadata (hidden, formats, folders)                          │
└─────────────────────────────────────────────────────────────────┘
                              ↓
                    [FABRIC EXTRACTOR]
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ OSI INTERMEDIATE REPRESENTATION (YAML)                           │
│ ├─ Semantic dataset definitions                                 │
│ ├─ DAX expressions (raw, unevaluated)                           │
│ ├─ Relationship specifications                                  │
│ ├─ Hierarchy definitions                                        │
│ └─ Metric dependencies                                          │
└─────────────────────────────────────────────────────────────────┘
                              ↓
        ┌──────────────────────────────────────────┐
        │  SEMANTIC TRANSLATION LAYER (NEW)        │
        │                                          │
        │  ┌─ DAX Parser & AST Engine             │
        │  ├─ Semantic Context Analyzer           │
        │  ├─ DAX Dependency Graph Builder        │
        │  ├─ M Query Transformer                 │
        │  ├─ SQL Generator (Snowflake)           │
        │  └─ Parity Validator                    │
        └──────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ SNOWFLAKE SEMANTIC RUNTIME                                      │
│ ├─ Dynamic Table (Semantic View)                                │
│ │  ├─ Fact table with dimension joins                           │
│ │  ├─ Computed measures (window functions)                      │
│ │  └─ Relationships preserved in DDL                           │
│ ├─ Metric Metadata Table                                        │
│ │  ├─ Measure definitions & SQL expressions                    │
│ │  ├─ Complexity tiers & DAX originals                         │
│ │  └─ Parity validation results                                │
│ ├─ Cortex Analyst Assets                                        │
│ │  ├─ YAML semantic model for NL queries                       │
│ │  └─ Measure metadata for AI context                          │
│ └─ Lineage & Dependency Tracking                                │
│    ├─ Measure-to-measure dependencies                          │
│    └─ Table-to-table relationships                             │
└─────────────────────────────────────────────────────────────────┘
```

### 5.2 Semantic Translation Layer Components

**Component 1: Enhanced DAX Parser**
```
Current:  Recursive-descent parser → AST
New:      Semantic-aware parser → Annotated AST
          ├─ Context requirements tagged
          ├─ Aggregation targets identified
          ├─ Dependency references resolved
          └─ Complexity tier assigned
```

**Component 2: Semantic Context Analyzer**
```
Purpose:  Understand what each measure means semantically
Input:    DAX expression + OSI model context
Output:   Semantic Intent (measurement target, aggregation type, context needs)

Example:
  DAX:     SUM([Amount])
  Context: Called in measure [Total Sales]
  Semantic Intent:
    - Aggregation Type: SUM
    - Aggregation Target: [Amount] column
    - Filter Context: Inherited from query
    - Row Context: None (set-based aggregation)
    - Time Intelligence: None
```

**Component 3: Semantic Dependency Graph**
```
Purpose:  Build complete map of measure interdependencies
Creates:
  - Measure → Measure dependencies
  - Measure → Column dependencies
  - Measure → Table dependencies
  - Measure → Relationship dependencies
  
Uses for:
  - Ordering measures for materialization
  - Detecting circular dependencies
  - Validating semantic validity
  - Optimizing query execution
```

**Component 4: M Query Transformer**
```
Current:  Extract M code, store as string
New:      Parse M, transform to SQL, validate equivalence

For each M query:
  1. Parse M code into AST
  2. Identify transformations (Filter, Transform, Select, etc.)
  3. Translate to SQL equivalents
  4. Preserve query folding hints
  5. Validate data type preservation
  6. Generate SQL + metadata
```

**Component 5: SQL Generation Engine**
```
Purpose:  Generate Snowflake SQL from translated DAX
Inputs:   Semantic Intent (from Analyzer) + SQL Generation Rules
Output:   Snowflake SQL expression

Features:
  - Template-based generation for common patterns
  - Window function synthesis for time intelligence
  - Join path resolution for relationship-aware queries
  - Null handling standardization
  - Type casting for cross-platform compatibility
```

**Component 6: Semantic Validator**
```
Purpose:  Ensure semantic equivalence before deployment
Tests:
  - DAX → SQL result parity (aggregate comparison)
  - Context fidelity (correct filter propagation)
  - Relationship semantics (join correctness)
  - Time intelligence accuracy (YTD calculations)
  - Null handling (BLANK vs NULL equivalence)

Generates:
  - Parity score (% of measures with <0.1% deviation)
  - Semantic risk report (which measures differ and why)
  - Validation test cases for regression testing
```

### 5.3 New Data Structures

**Semantic Intent Model**
```python
@dataclass
class SemanticIntent:
    """Represents the semantic meaning of a DAX expression."""
    aggregation_type: str  # SUM, COUNT, AVG, etc.
    aggregation_targets: List[str]  # Columns being aggregated
    filter_context_type: str  # "inherited", "explicit", "modified"
    row_context_required: bool  # Does expression need row-by-row evaluation?
    time_intelligence_type: Optional[str]  # TOTALYTD, SAMEPERIODLASTYEAR, etc.
    relationship_references: List[str]  # Relationships needed
    context_transitions: List[str]  # ALL, FILTER, etc.
    complexity_tier: int  # 1-5 (5 = requires LLM)
    translation_strategy: str  # "deterministic", "ast_based", "llm_fallback"
    semantic_risks: List[str]  # Potential parity risks
```

**Semantic Dependency Graph**
```python
@dataclass
class SemanticGraph:
    """Complete dependency graph for a semantic model."""
    nodes: Dict[str, SemanticNode]  # Measures, columns, tables
    edges: List[SemanticEdge]  # Dependencies
    cycles: List[List[str]]  # Circular dependencies
    materialization_order: List[str]  # Topologically sorted measures
    conflict_zones: List[str]  # Areas where parity may fail
```

**Parity Validation Result**
```python
@dataclass
class ParityValidationResult:
    """Result of semantic equivalence validation."""
    overall_parity_score: float  # 0-100%
    measure_results: Dict[str, MeasureValidation]
    passed_measures: int
    failed_measures: int
    risk_measures: int  # Measures flagged for manual review
    validation_queries: List[str]  # SQL queries used for validation
    recommendations: List[str]  # Actions to fix failures
```

---

## 6. DAX Translation Strategy

### 6.1 Translation Tier System (Enhanced)

**Current Tier System:**
- Tier 1: Direct aggregations
- Tier 2: Simple CALCULATE + arithmetic
- Tier 3: Time intelligence
- Tier 4: Complex CALCULATE/FILTER/ALL
- Tier 5: LLM fallback

**Enhanced Tier System for Semantic Execution:**

**Tier 1: Deterministic Direct Aggregations** ✅
```
Patterns:  SUM([X]), COUNT([Y]), DISTINCTCOUNT([Z]), etc.
Strategy:  Regex + context inference
Result:    Exact SQL match
Examples:
  DAX:     SUM([Amount])
  SQL:     SUM("AMOUNT")

  DAX:     DISTINCTCOUNT([Customer ID])
  SQL:     COUNT(DISTINCT "CUSTOMER_ID")
```

**Tier 2: Context-Aware Arithmetic** ✅
```
Patterns:  [M1] + [M2], CALCULATE([M1] * 1.1), IF([X] > 0, [Y], 0)
Strategy:  AST-based pattern matching + inline dependencies
Result:    SQL with computed columns and CASE expressions
Examples:
  DAX:     [Net Sales] = [Gross Sales] - [Discounts]
  SQL:     (SUM("GROSS_SALES")) - (SUM("DISCOUNTS"))

  DAX:     IF([Qty] = 0, 0, DIVIDE([Sales], [Qty]))
  SQL:     CASE WHEN SUM("QTY") = 0 THEN 0 
           ELSE DIV0(SUM("SALES"), SUM("QTY")) END
```

**Tier 3: Time Intelligence (Window Functions)** ✅
```
Patterns:  TOTALYTD([X], [Date]), SAMEPERIODLASTYEAR([X], [Date])
Strategy:  Calendar detection + window function synthesis
Result:    Snowflake window functions with PARTITION BY date parts
Examples:
  DAX:     TOTALYTD(SUM([Sales]), [Date])
  SQL:     SUM("SALES") OVER (
             PARTITION BY YEAR("DATE")
             ORDER BY "DATE"
             ROWS BETWEEN UNBOUNDED PRECEDING 
             AND CURRENT ROW
           )
```

**Tier 4: Complex Context Modification** ⚠️
```
Patterns:  CALCULATE(X, FILTER(...)), CALCULATE(X, ALL(...)), ALLEXCEPT(...)
Strategy:  Context stack analysis + relationship-aware SQL generation
Result:    Subqueries or modified JOIN conditions
Examples:
  DAX:     CALCULATE(SUM([Sales]), 
             FILTER(ALL(Region), [Region]="USA"))
  SQL:     (SELECT SUM("SALES")
            FROM sales_fact sf
            WHERE "REGION" = 'USA')

Challenges:
  - Nested FILTER/CALCULATE: may need LLM
  - Complex ALL/ALLEXCEPT: requires relationship knowledge
  - Context stacking: SQL cannot represent directly
```

**Tier 5: Row Context Iterators** 🔄
```
Patterns:  SUMX(table, expr), AVERAGEX, COUNTX, RANKX, EARLIER
Strategy:  LLM-assisted + validation
Result:    Window functions or subqueries (best effort)
Examples:
  DAX:     SUMX(FILTER(Orders, [Amount]>100), [Amount])
  SQL:     (SELECT SUM("AMOUNT")
            FROM orders
            WHERE "AMOUNT" > 100)
  
  BUT: How to handle EARLIER?
  DAX:     RANKX(ALL(Sales), [Total Sales], ,DESC)
  SQL:     ??? (No exact equivalent; use ROW_NUMBER()?)
           ROW_NUMBER() OVER (
             ORDER BY SUM("SALES") DESC
           )
```

**Tier 6: Unsupported Patterns** ❌
```
Patterns:  Complex row context logic, custom M functions
Strategy:  Error + user guidance
Result:    Deployment blocked; user must refactor or accept fallback
```

### 6.2 Deterministic vs LLM-Assisted Translation Decision Tree

```
DAX Expression
    ↓
┌─────────────────────────────────────────┐
│ Is it Tier 1 pattern?                   │
│ (Direct aggregation: SUM, COUNT, etc.)  │
└─────────────────────────────────────────┘
    YES ──→ DETERMINISTIC: Regex match
    NO
    ↓
┌─────────────────────────────────────────┐
│ Is it Tier 2 pattern?                   │
│ (Arithmetic, IF/SWITCH, DIVIDE)         │
└─────────────────────────────────────────┘
    YES ──→ DETERMINISTIC: AST-based
    NO
    ↓
┌─────────────────────────────────────────┐
│ Is it Tier 3 pattern?                   │
│ (TOTALYTD, SAMEPERIODLASTYEAR, etc.)    │
└─────────────────────────────────────────┘
    YES ──→ DETERMINISTIC: Window function synthesis
    NO
    ↓
┌─────────────────────────────────────────┐
│ Is it Tier 4 pattern?                   │
│ (Complex CALCULATE, FILTER, ALL)        │
└─────────────────────────────────────────┘
    YES ──→ DETERMINISTIC (if simple) / LLM (if complex)
    NO
    ↓
┌─────────────────────────────────────────┐
│ Is it Tier 5 pattern?                   │
│ (Iterators, RANKX, EARLIER, etc.)       │
└─────────────────────────────────────────┘
    YES ──→ LLM-ASSISTED: Generate + validate
    NO
    ↓
┌─────────────────────────────────────────┐
│ Unknown/Unsupported                     │
└─────────────────────────────────────────┘
    ──→ ERROR: Block deployment, require refactor
```

### 6.3 Detailed DAX Pattern Translation Strategy

**CALCULATE Handling**

Current behavior: Partial support for simple CALCULATE

Future behavior: Full context-aware CALCULATE translation

**Strategy:**
1. Parse CALCULATE filter arguments
2. Identify filter type: FILTER, ALL, ALLEXCEPT, comparison, or combination
3. Generate appropriate SQL modifier:
   - FILTER → WHERE clause
   - ALL → OVER() window (reset context)
   - ALLEXCEPT → PARTITION BY clause
   - Comparison → WHERE + inline condition

**Example Translations:**
```
CALCULATE(SUM([Amount]), [Region] = "USA")
→ WHERE region_id = (SELECT id FROM region WHERE name='USA')

CALCULATE(SUM([Amount]), ALL([Date]))
→ SUM(...) OVER ()  -- full table sum

CALCULATE(SUM([Amount]), ALLEXCEPT([Category]))
→ SUM(...) OVER (PARTITION BY category_id)

CALCULATE(
  SUM([Amount]), 
  FILTER(ALL(Orders), [Order Date] > DATE(2024,1,1))
)
→ (SELECT SUM(amount) FROM orders WHERE order_date > '2024-01-01')
```

**FILTER Handling**

**Strategy:**
1. Extract table being filtered: FILTER(table_name, predicate)
2. Translate predicate to SQL WHERE condition
3. Resolve column references to physical columns
4. Apply type-specific filtering logic

**Example:**
```
FILTER(Orders, [Amount] > 1000 AND [Status] = "Completed")
→ SELECT * FROM orders 
  WHERE amount > 1000 AND status = 'Completed'
```

**ALL Handling**

**Strategy:**
1. Determine scope of ALL: ALL(table) or ALL(column)
2. Generate context-resetting mechanism:
   - In window function: OVER()
   - In subquery: no WHERE clause
   - In join: remove filter predicates

**Example:**
```
CALCULATE(SUM([Amount]), ALL(Region))
→ SUM(amount) OVER ()  -- Ignore all region filters

CALCULATE(SUM([Amount]), ALL(Region[Country]))
→ Would need: SUM(amount) OVER (PARTITION BY region_id)
  -- Keep region, remove country within region
```

**SUMX / AVERAGEX / COUNTX Handling**

**Current:** Falls back to LLM

**Future (Phase 1):** Deterministic for simple patterns

**Strategy:**
1. Detect if iterator is over filtered table or computed table
2. If simple filter: convert to SQL GROUP BY with aggregate
3. If complex computation in expression: use LLM

**Examples:**
```
SUMX(FILTER(Orders, [Amount] > 100), [Amount])
→ SELECT SUM(amount) FROM orders WHERE amount > 100
  (Simplified: same as SUM([Amount]) with filter)

SUMX(FILTER(Orders, [Region] = "USA"), 
     DIVIDE([Amount], [Units]))
→ SELECT SUM(DIV0(amount, units))
  FROM orders
  WHERE region_id = (SELECT id FROM region WHERE name='USA')

SUMX(Orders, [Amount] * [Commission Rate])
→ ???  (Need expression evaluation; defer to LLM)
```

**RANKX Handling**

**Current:** Falls back to LLM

**Future (Phase 2):** Deterministic for simple patterns

**Strategy:**
1. Identify ranking column and sort order
2. Generate ROW_NUMBER or RANK window function
3. Handle ties appropriately

**Example:**
```
RANKX(ALL(Products), [Sales], , DESC)
→ ROW_NUMBER() OVER (
    ORDER BY (SUM(sales)) DESC
  )

RANKX(
  FILTER(Products, [Category] = "Electronics"),
  [Sales],
  ,
  DESC
)
→ ROW_NUMBER() OVER (
    PARTITION BY category_id
    ORDER BY SUM(sales) DESC
  )
```

**EARLIER Handling**

**Current:** Falls back to LLM

**Status:** Too complex for Phase 1; defer to Phase 2+

**Challenge:** EARLIER references previous row in iteration context
```
EARLIER([Amount], 1)  -- Get previous row's Amount value
```

SQL has no equivalent. Would need:
```sql
LAG(amount) OVER (ORDER BY some_column)
```

But "some_column" is ambiguous. Must infer from context.

**TOTALYTD and Time Intelligence Specifics**

**Current:** Partial support for standard calendar

**Future:** Fiscal calendar detection + auto-translation

**Strategy:**
1. Detect date column type (DATE, DATETIME, or ID)
2. Look for calendar table or date hierarchy
3. Infer year boundary (Jan 1 or custom)
4. Generate window function with correct PARTITION BY and ORDER BY

**Examples:**
```
TOTALYTD(SUM([Sales]), [Date])
[Standard Calendar]
→ SUM(sales) OVER (
    PARTITION BY YEAR(date_column)
    ORDER BY date_column
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
  )

[Fiscal Calendar: Starts Jul 1]
→ SUM(sales) OVER (
    PARTITION BY YEAR(
      DATEADD(MONTH, -6, date_column)
    )
    ORDER BY date_column
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
  )
```

**SAMEPERIODLASTYEAR Handling**

**Strategy:**
1. Identify date column
2. Generate DATEADD-based subquery or window function
3. Handle multiple date granularities (year, quarter, month)

**Examples:**
```
SAMEPERIODLASTYEAR(SUM([Sales]), [Date])
→ SUM(sales) FILTER (
    WHERE DATEDIFF(YEAR, date, current_date) = 1
  )

More accurately (using subquery):
→ (SELECT SUM(sales)
  FROM sales
  WHERE DATE_TRUNC('YEAR', date) = 
        DATE_TRUNC('YEAR', DATEADD(YEAR, -1, current_date)))
```

### 6.4 Semantic Equivalence Guarantees

**What We Guarantee:**
1. ✅ **Aggregation Correctness:** SUM, COUNT, AVG produce <0.1% deviation
2. ✅ **Filter Fidelity:** WHERE clauses match logical intent
3. ✅ **Relationship Semantics:** Joins preserve many-to-one cardinality
4. ✅ **Time Boundaries:** YTD calculations use same date boundaries

**What We Cannot Guarantee:**
1. ❌ **Row Context Semantics:** EARLIER, complex iterators may differ
2. ⚠️ **Floating-Point Precision:** Different SQL engines may round differently
3. ⚠️ **NULL Handling:** DAX BLANK vs SQL NULL may behave differently
4. ⚠️ **Type Coercion:** Implicit type conversions may differ

**Failure Handling:**
- Deterministic failures: Block deployment with clear error message
- Semantic mismatches: Flag as warning, provide validation queries
- Performance issues: Recommend materialization or refactoring

---

## 7. M Query / Power Query Strategy

### 7.1 M Query Extraction & Analysis

**Current State:**
- M code extracted from partitions
- Stored as string in source_expression field
- Not analyzed or transformed

**Future State:**
- Parse M code into AST
- Identify transformation patterns
- Classify as simple/complex
- Attempt SQL translation for simple cases

### 7.2 M Query Classification

**Simple M Queries (Candidates for Translation)**
```
Patterns:
  1. Simple SELECT: Source → Table.Select({cols})
  2. Simple FILTER: Table.SelectRows(..., each [X] = value)
  3. Simple TRANSFORM: Table.TransformColumns(..., {transformations})
  4. Simple JOIN: Table.NestedJoin(...)

Characteristics:
  - No loops or conditionals
  - No custom functions
  - Deterministic filtering/selection
  - Data type conversions only

Examples:
  let
    Source = Snowflake.Databases(...),
    Filtered = Table.SelectRows(Source, each [Year] >= 2020)
  in
    Filtered
  
  →SQL: SELECT * FROM source WHERE year >= 2020
```

**Complex M Queries (Will Not Translate)**
```
Patterns:
  1. Custom functions defined
  2. Conditionals (if/then/else)
  3. Loops or iterative logic
  4. External data calls
  5. Complex transformations

Examples:
  let
    CustomFunc = (x) => if x > 0 then x * 2 else x,
    Data = Snowflake.Databases(...),
    Transformed = Table.TransformColumns(
      Data, 
      {"Amount", each CustomFunc([_])}
    )
  in
    Transformed
  
  →Can't translate; requires M execution engine
```

### 7.3 M Query Transformation Strategy

**Supported Transformations:**

**1. Table Selection**
```m
Table.SelectColumns(source, {"Col1", "Col2"})
→ SELECT "COL1", "COL2" FROM source
```

**2. Row Filtering**
```m
Table.SelectRows(source, each [Year] = 2024)
→ SELECT * FROM source WHERE "YEAR" = 2024
```

**3. Column Renaming**
```m
Table.RenameColumns(source, {{"OldName", "NewName"}})
→ SELECT "OldName" AS "NewName" FROM source
```

**4. Type Conversion**
```m
Table.TransformColumns(source, {{"Amount", Currency.From}})
→ SELECT CAST("Amount" AS NUMERIC) FROM source
```

**5. Simple Joins**
```m
Table.NestedJoin(sales, "CustID", customers, "ID", "Customers")
→ SELECT s.*, c.*
  FROM sales s
  LEFT JOIN customers c ON s.CustID = c.ID
```

**Not Supported:**
- Custom M functions
- Conditional logic
- Recursive transformations
- External API calls

### 7.4 Parameter Handling

**Current:** Parameters extracted but not resolved

**Future:** Parameter binding strategy

**Strategy:**
1. Identify Power Query parameters (e.g., @StartDate)
2. Map to Snowflake native parameters (if possible)
3. Generate SQL with parameter placeholders
4. Document required parameter values

**Example:**
```m
// Fabric Power Query with parameter:
let
    StartDate = #date(2024, 1, 1),  // Could be parameterized
    Data = Snowflake.Databases(...),
    Filtered = Table.SelectRows(Data, each [Date] >= StartDate)
in
    Filtered

→ Snowflake SQL:
  SELECT * FROM source WHERE date >= ?  -- ? = parameter placeholder
  
  Metadata: Parameter "StartDate" = DATE, default 2024-01-01
```

### 7.5 Query Folding Preservation

**Goal:** Ensure query folding optimization is preserved when translating to Snowflake

**Strategy:**
1. Identify pushdown-able filters (can execute at source)
2. Generate WHERE clause in source query, not application layer
3. Avoid unnecessary column selections
4. Minimize post-fetch transformations

**Example:**
```m
[POOR - No folding]
let
    All Data = Snowflake.Databases(...),
    Everything = All Data{[Name="ORDERS"]}[Data],
    Filtered = Table.SelectRows(Everything, each [Year] = 2024)
in
    Filtered

→ Fetches all data to client, then filters
→ Very slow!

[GOOD - Query folding]
→ Snowflake Native Query: 
  SELECT * FROM ORDERS WHERE YEAR = 2024
→ Only 2024 data fetched
→ Much faster!
```

---

## 8. Semantic Runtime Design

### 8.1 Execution Model

**Decision: Hybrid Approach (Not Single Approach)**

Options Considered:
1. **Semantic Views Only** - All logic in DDL
2. **Dynamic Tables** - Auto-refreshing views
3. **Stored Procedures** - Callable functions
4. **Metadata-Driven Execution** - Computed at query time

**Chosen: Hybrid**
- Use **Dynamic Tables** for materialized semantic views
- Use **Stored Procedures** for complex measure calculations
- Use **Metadata Tables** for measure definitions
- Use **SQL UDF** for simple translations

**Why Hybrid:**
- Dynamic tables: Fast queries, auto-refresh, relationship support
- Stored procedures: Complex logic, reusable, testable
- Metadata: Extensible, version-able, auditable
- SQL UDFs: Performance, composability

### 8.2 Dynamic Table Strategy

**Primary Semantic View:**
```sql
CREATE OR REPLACE DYNAMIC TABLE SEMANTIC_MODEL_V1
  TARGET LAG = '1 hour'
  WAREHOUSE = COMPUTE_WH
AS
SELECT
    -- Dimension columns from fact table
    f.order_id,
    f.order_date,
    f.customer_id,
    f.product_id,
    f.amount,
    
    -- Joined dimension columns
    c.customer_name,
    c.customer_segment,
    p.product_name,
    p.product_category,
    d.calendar_year,
    d.calendar_quarter,
    d.calendar_month,
    d.is_weekend,
    
    -- Computed measures (materialized)
    SUM(f.amount) OVER () as total_sales,
    SUM(f.amount) OVER (PARTITION BY f.customer_id) as customer_total,
    COUNT(*) OVER () as total_orders,
    
    -- Time intelligence (window functions)
    SUM(f.amount) OVER (
      PARTITION BY d.calendar_year
      ORDER BY f.order_date
      ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) as year_to_date_sales,
    
    -- Relationship preservation
    ROW_NUMBER() OVER (ORDER BY f.order_date) as _order_sequence
FROM fact_sales f
LEFT JOIN dim_customer c ON f.customer_id = c.customer_id
LEFT JOIN dim_product p ON f.product_id = p.product_id
LEFT JOIN dim_date d ON f.order_date = d.date
;
```

**Advantages:**
- ✅ Auto-refreshing (no manual maintenance)
- ✅ Relationships visible in SELECT * queries
- ✅ Measures available as columns
- ✅ Time intelligence computed efficiently

**Limitations:**
- ⚠️ Cannot handle dynamic context (all context baked in)
- ⚠️ Cannot execute complex CALCULATE patterns at query time
- ⚠️ Materialization costs (storage, refresh time)

### 8.3 Complex Measure Resolution

**For measures that cannot be materialized in dynamic table:**

Use **Stored Procedures** or **SQL UDFs**

**Strategy:**
1. Classify each measure: "simple" (materializable) vs "complex" (deferred)
2. Materialize simple measures in dynamic table
3. Create UDFs for complex measures
4. Query-time execution: dynamic table + UDF calls

**Example:**

```sql
-- Measure 1: Simple (materialized in dynamic table)
SUM(amount) -- Available as "total_sales" column

-- Measure 2: Complex (complex CALCULATE with FILTER)
CREATE OR REPLACE FUNCTION CALC_TOP_CUSTOMER_SALES()
RETURNS FLOAT
AS
$$
  SELECT SUM(amount)
  FROM semantic_model_v1
  WHERE customer_segment = 'VIP'
  AND calendar_year = YEAR(CURRENT_DATE())
$$
;

-- Usage in query:
SELECT
    customer_name,
    total_sales,  -- From dynamic table
    CALC_TOP_CUSTOMER_SALES() as vip_sales  -- From UDF
FROM semantic_model_v1
```

### 8.4 Metadata-Driven Execution

**Measure Metadata Table:**
```sql
CREATE TABLE IF NOT EXISTS _SEMANTIC_MEASURES (
    measure_id VARCHAR PRIMARY KEY,
    measure_name VARCHAR,
    dataset_name VARCHAR,
    dax_expression VARCHAR,  -- Original DAX
    sql_expression VARCHAR,  -- Translated SQL
    execution_type VARCHAR,  -- 'materialized', 'udf', 'computed'
    complexity_tier INT,  -- 1-5
    aggregation_type VARCHAR,  -- SUM, COUNT, AVG, etc.
    is_time_intelligence BOOLEAN,
    time_intel_type VARCHAR,  -- TOTALYTD, SAMEPERIODLASTYEAR, etc.
    dependencies VARCHAR,  -- Comma-separated measure names
    parity_score FLOAT,  -- % of validation tests passed
    last_validated TIMESTAMP,
    validation_queries VARCHAR,  -- JSON array of test queries
    metadata JSON  -- Additional semantic metadata
);
```

**Purpose:**
- Versioning measure definitions
- Tracking parity scores
- Enabling audit trails
- Supporting measure discovery
- Enabling metadata-driven optimizations

### 8.5 Relationship Preservation in Runtime

**How Relationships Execute:**

**Option A: Baked into Dynamic Table (Current)**
```sql
SELECT *
FROM semantic_model_v1
-- Relationships already joined; user sees denormalized data
```
Pros: Simple, fast
Cons: Cannot apply dynamic relationship switching

**Option B: Explicit Join Paths (Future)**
```sql
SELECT *
FROM fact_sales
LEFT JOIN dim_customer ON fact_sales.customer_id = dim_customer.customer_id
LEFT JOIN dim_product ON fact_sales.product_id = dim_product.product_id
-- Relationships explicit; can be modified dynamically
```
Pros: Flexible, dynamic
Cons: More complex queries, must educate users

**Recommendation (Phase 1):**
- Use Option A (baked-in)
- Document join paths in metadata
- Transition to Option B in Phase 2 if needed

---

## 9. Validation & Parity Framework

### 9.1 Semantic Equivalence Validation

**Goal:** Prove that Snowflake results match Fabric results within acceptable tolerance

**Approach: Multi-Layer Validation**

**Layer 1: Schema Validation**
```
Verify:
  - All tables exist in Snowflake
  - All columns present and typed correctly
  - Relationships defined correctly
  - No missing measures
```

**Layer 2: Aggregate Parity**
```
For each measure:
  Fabric: SELECT <measure>
  Snowflake: SELECT <translated_measure>
  
  Compare:
    - Result values (must be <0.1% deviation)
    - NULL counts (must match)
    - Distinct counts (must match for dimension columns)
```

**Layer 3: Context Fidelity**
```
Test filter propagation:
  Fabric: WHERE region="USA" AND year=2024
  Snowflake: Same WHERE clause on semantic view
  
  Result: Measure values must match
```

**Layer 4: Time Intelligence Accuracy**
```
For each time intelligence measure:
  Fabric: TOTALYTD(measure, date)
  Snowflake: Equivalent window function
  
  Compare:
    - YTD accumulation matches
    - Period boundaries match
    - Edge cases (year boundaries, missing dates)
```

**Layer 5: Relationship Semantics**
```
Test join correctness:
  Verify many-to-one cardinality preserved
  Verify cross-filter direction honored
  Verify no duplicate rows from bad joins
```

### 9.2 DAX vs SQL Comparison Engine

**Architecture:**

```
┌────────────────────────────────────────┐
│ COMPARISON ENGINE                      │
├────────────────────────────────────────┤
│                                        │
│  Input: Measure Definition             │
│    - DAX expression                    │
│    - Translated SQL expression         │
│    - Test context (filters, etc.)      │
│                                        │
│  ↓                                     │
│                                        │
│  FABRIC EXECUTION RUNNER               │
│    - Parse DAX                         │
│    - Evaluate in Fabric                │
│    - Capture result                    │
│                                        │
│  SNOWFLAKE EXECUTION RUNNER            │
│    - Execute SQL                       │
│    - Capture result                    │
│                                        │
│  COMPARISON LOGIC                      │
│    - Compare results                   │
│    - Calculate deviation               │
│    - Generate report                   │
│                                        │
│  Output: Parity Report                 │
│    - Pass/Fail                         │
│    - Deviation %                       │
│    - Recommendations                   │
│                                        │
└────────────────────────────────────────┘
```

**Implementation:**

```python
class SemanticComparisonEngine:
    """Compares DAX and SQL execution for semantic equivalence."""
    
    def compare_measure(
        self,
        measure_name: str,
        dax_expression: str,
        sql_expression: str,
        test_contexts: List[Dict[str, Any]]
    ) -> ParityReport:
        """
        Compare DAX vs SQL for given measure across test contexts.
        
        Args:
            measure_name: Name of measure
            dax_expression: Original DAX
            sql_expression: Translated SQL
            test_contexts: List of filter contexts to test
        
        Returns:
            ParityReport with pass/fail and deviation %
        """
        results = []
        
        for context in test_contexts:
            # Execute in Fabric (via API)
            fabric_result = self.execute_in_fabric(
                dax_expression,
                context
            )
            
            # Execute in Snowflake
            snowflake_result = self.execute_in_snowflake(
                sql_expression,
                context
            )
            
            # Compare results
            deviation = self.calculate_deviation(
                fabric_result,
                snowflake_result
            )
            
            results.append({
                'context': context,
                'fabric_result': fabric_result,
                'snowflake_result': snowflake_result,
                'deviation_pct': deviation,
                'passed': deviation < 0.1
            })
        
        return ParityReport(measure_name, results)
```

### 9.3 Test Case Generation

**Automated Test Generation Strategy:**

```
For each measure:
  1. Identify input columns
  2. Generate test contexts:
     - No filters (baseline)
     - Single-column filters (each column)
     - Multi-column filters (combinations)
     - Edge cases (NULL, zero, negative values)
     - Time-based filters (if time intel measure)
  
  3. Execute in both systems
  4. Compare results
  5. Flag mismatches
```

**Example:**

Measure: `Total Sales = SUM([Amount])`
Columns: Customer, Region, Year

Test Cases:
```
1. No filters
   Expected: SUM(amount) for all rows
   
2. Customer = "ABC Corp"
   Expected: SUM(amount) for that customer
   
3. Region = "USA"
   Expected: SUM(amount) for USA region
   
4. Year = 2024
   Expected: SUM(amount) for 2024
   
5. Customer = "ABC" AND Year = 2024
   Expected: SUM(amount) for that customer in 2024
   
6. NULL handling
   Expected: SUM excludes NULL amounts
```

### 9.4 Regression Framework

**Purpose:** Prevent parity regressions in future updates

**Approach:**

```
1. Baseline Phase (Initial Validation)
   - Execute all test cases
   - Record results as "golden" baseline
   - Store baseline in version control
   
2. Change Phase (After Updates)
   - Re-execute all test cases
   - Compare against baseline
   - Flag any deviations
   - Fail deployment if regressions detected
   
3. Investigation Phase (If Regression)
   - Identify which measure failed
   - Run parity engine for that measure
   - Determine root cause
   - Fix or revert change
```

**Implementation:**

```python
class RegressionFramework:
    """Manages regression testing for semantic parity."""
    
    def establish_baseline(self):
        """Run all tests and save baseline."""
        baseline = {}
        for measure in all_measures:
            report = self.compare_measure(measure)
            baseline[measure.name] = report
        
        self.save_baseline(baseline)
        return baseline
    
    def detect_regressions(self):
        """Run tests and compare to baseline."""
        current = {}
        regressions = []
        
        for measure in all_measures:
            report = self.compare_measure(measure)
            current[measure.name] = report
            
            baseline_report = self.load_baseline(measure.name)
            
            if not self.is_equivalent(report, baseline_report):
                regressions.append(measure.name)
        
        if regressions:
            raise RegressionError(
                f"Regressions detected: {regressions}"
            )
        
        return current
```

---

## 10. Dependency Resolution & Metadata

### 10.1 Enhanced Dependency Graph

**Current State:**
- Measure-to-measure dependencies tracked
- Basic cycle detection

**Future State:**
- Complete semantic dependency graph
- Topology-aware materialization ordering
- Impact analysis for changes

**Graph Components:**

```
Nodes:
  - Measures
  - Columns
  - Tables
  - Relationships
  - Hierarchies

Edges (Dependencies):
  - Measure → Measure (M1 uses M2)
  - Measure → Column (M1 aggregates Col1)
  - Measure → Table (M1 aggregates from Table1)
  - Measure → Relationship (M1 needs Rel1 active)
  - Column → Table (Col1 from Table1)
  - Relationship → Table (Rel1 links Table1-Table2)

Metadata per Edge:
  - Dependency Type: strong, weak, optional
  - Cardinality: one-to-one, one-to-many, many-to-many
  - Join Type: LEFT, INNER, FULL
  - Filter Propagation: yes, no
```

### 10.2 Materialization Ordering

**Problem:**
```
If [Net Sales] = [Gross Sales] - [Returns],
Then [Gross Sales] and [Returns] must be computed first.
```

**Solution: Topological Sort**

```
1. Build dependency graph
2. Perform topological sort (depth-first)
3. Result: Measures ordered such that all dependencies computed first
4. Detect cycles → error

Example:
  [A] = SUM(amount)
  [B] = [A] + [C]
  [C] = [B] * 2  ← Circular! Error.
```

**Algorithm:**

```python
def topological_sort(graph: SemanticGraph) -> List[str]:
    """Sort measures by dependencies using DFS."""
    visited = set()
    visiting = set()
    order = []
    
    def visit(node):
        if node in visited:
            return
        if node in visiting:
            raise CyclicDependencyError(f"Cycle detected at {node}")
        
        visiting.add(node)
        for dependent in graph.get_dependents(node):
            visit(dependent)
        visiting.remove(node)
        
        visited.add(node)
        order.append(node)
    
    for node in graph.nodes:
        if node not in visited:
            visit(node)
    
    return order
```

### 10.3 Impact Analysis

**Purpose:** When measure definition changes, determine what else must be recomputed

**Algorithm:**

```
Change: Modify measure [A]
  ↓
Find all measures that depend on [A]:
  - [B] = [A] + [C]  ← Needs recomputation
  - [D] = [B] * 2   ← Needs recomputation (transitive)
  ↓
Flag for revalidation:
  - Re-test [A], [B], [D]
  - Check parity regression
  - Update metadata
```

### 10.4 Metadata Synchronization

**Challenge:** Keep metadata in sync as model evolves

**Strategy:**

```
1. Versioning
   - Track measure definition versions
   - Store before/after DAX for changes
   - Enable rollback if needed

2. Change Tracking
   - Record who changed what, when
   - Link to deployment
   - Enable audit trail

3. Dependency Tracking
   - Update dependency graph on any change
   - Detect new cycles
   - Flag breaking changes

4. Parity Tracking
   - Re-validate measures after changes
   - Track parity score trends
   - Alert on regressions
```

**Implementation:**

```sql
CREATE TABLE _SEMANTIC_CHANGE_LOG (
    change_id INT PRIMARY KEY AUTO_INCREMENT,
    measure_id VARCHAR,
    change_type VARCHAR,  -- 'created', 'modified', 'deleted'
    dax_before VARCHAR,
    dax_after VARCHAR,
    changed_by VARCHAR,
    changed_at TIMESTAMP,
    deployment_id VARCHAR,
    parity_before FLOAT,
    parity_after FLOAT,
    notes VARCHAR
);
```

---

## 11. Scalability & Performance

### 11.1 Caching Strategy

**Translation Caching:**
```
Problem: Translating 1000 measures takes time
Solution: Cache DAX → SQL translations

Implementation:
  Key: (dax_expression, model_id)
  Value: (sql_expression, metadata, parity_score)
  
  Cache Hit Rate Expected: 60-70% (similar measures repeat)
  Time Saved: 100-200ms per measure
```

**Validation Caching:**
```
Problem: Re-validating all measures after each change is expensive
Solution: Cache validation results

Implementation:
  Cache test results for each measure
  Invalidate on:
    - Measure definition change
    - Column type change
    - Relationship definition change
    - New schema version
  
  Incremental validation:
    - Only re-test changed measures + dependent measures
    - Can reduce validation time from minutes to seconds
```

### 11.2 Incremental Sync Strategy

**Problem:**
```
Syncing 5000-measure model takes 30+ minutes
User only changed 2 measures
```

**Solution:**

```
1. Detect changed measures (via DuckDB history)
2. Rebuild dependency graph
3. Identify affected measures (dependent + transitively dependent)
4. Translate only affected measures
5. Validate only affected measures
6. Deploy only affected measures
7. Time: 30 min → 30 sec for small changes
```

**Implementation:**

```python
def incremental_sync(
    old_model: OSIModel,
    new_model: OSIModel,
    cache: TranslationCache
) -> SyncResult:
    """
    Sync only changed measures, not entire model.
    """
    # 1. Detect changes
    changes = detect_changes(old_model, new_model)
    changed_measures = set(changes.keys())
    
    # 2. Build dependency graph
    graph = build_dependency_graph(new_model)
    
    # 3. Find affected measures
    affected = set()
    for measure in changed_measures:
        affected.add(measure)
        affected.update(graph.get_all_dependents(measure))
    
    # 4. Translate affected measures
    for measure in affected:
        try:
            # Try cache first
            if measure in cache:
                sql = cache[measure]
            else:
                sql = translate_dax(measure.dax)
                cache[measure] = sql
            
            measure.sql_expression = sql
        except TranslationError as e:
            log.warning(f"Failed to translate {measure.name}: {e}")
    
    # 5. Validate affected measures
    results = validate_measures(affected, new_model)
    
    # 6. Deploy to Snowflake
    if results.all_passed:
        deploy_measures(affected, new_model)
    else:
        log.error(f"Validation failed for: {results.failed}")
        raise ValidationError(results.failed)
    
    return results
```

### 11.3 Parallel Translation

**Problem:**
```
Sequential translation of 1000 measures: 1000 * 100ms = 100 seconds
```

**Solution:**

```
Parallel translation (8 workers): 1000 / 8 * 100ms ≈ 12 seconds
```

**Implementation:**

```python
from concurrent.futures import ThreadPoolExecutor

def parallel_translate_measures(
    measures: List[SMLMetric],
    num_workers: int = 8
) -> Dict[str, str]:
    """
    Translate measures in parallel.
    """
    translations = {}
    
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {
            executor.submit(translate_dax, measure.dax): measure.name
            for measure in measures
        }
        
        for future in futures:
            measure_name = futures[future]
            try:
                sql = future.result(timeout=30)
                translations[measure_name] = sql
            except Exception as e:
                log.error(f"Failed to translate {measure_name}: {e}")
    
    return translations
```

**Constraints:**
- Don't exceed Snowflake concurrent query limit (10-20 queries)
- Batch LLM calls (use batch API if available)
- Monitor resource utilization

### 11.4 Materialization Strategy

**Measure Materialization Tiers:**

**Tier 1: Always Materialize (Simple, Frequently Used)**
```
Examples:
  - SUM([Amount])
  - COUNT([ID])
  - AVERAGE([Price])

Benefits:
  - Fast queries (already computed)
  - No runtime overhead

Strategy:
  - Compute in dynamic table
  - Store as regular column
  - Index if used in WHERE clause
```

**Tier 2: Cache if Large (Complex, Sometimes Used)**
```
Examples:
  - Complex CALCULATE expressions
  - Time intelligence measures
  - Aggregations over large fact tables

Strategy:
  - Compute on-demand, cache result
  - Refresh periodically
  - Clear cache on fact table change
```

**Tier 3: Compute At Query Time (Ad-Hoc, Rarely Used)**
```
Examples:
  - User-defined calculations
  - One-off measures
  - Debugging measures

Strategy:
  - Generate SQL function
  - Call from query
  - Accept slower performance
```

### 11.5 Performance Optimization

**Query Optimization:**

```sql
-- BEFORE: Full table scan
SELECT
    customer_name,
    SUM(amount) as total_sales
FROM semantic_view
WHERE year = 2024
GROUP BY customer_id, customer_name;

-- AFTER: Use materialized view
SELECT
    customer_name,
    customer_total_sales_2024
FROM semantic_view_2024
ORDER BY customer_total_sales_2024 DESC;

Time: 2 seconds → 100ms
```

**Indexing Strategy:**

```sql
-- Index frequently filtered columns
CREATE INDEX idx_region ON semantic_view(region_id);
CREATE INDEX idx_year ON semantic_view(calendar_year);
CREATE INDEX idx_date ON semantic_view(order_date);

-- Cluster by common dimension
CLUSTER BY region_id, calendar_year;
```

---

## 12. Security & Governance

### 12.1 RLS (Row-Level Security) Strategy

**Phase 1: Metadata Documentation**
- Document RLS requirements from Fabric model
- Store in _SEMANTIC_RLS_RULES table
- Generate Snowflake policy templates

**Phase 2: Automatic RLS Generation**
- Generate ROW ACCESS POLICY automatically
- Map Fabric RLS to Snowflake policies
- Test RLS enforces correctly

**Example:**

```sql
-- Fabric RLS: Sales rep only sees their region
-- Translated to Snowflake:

CREATE ROW ACCESS POLICY sales_rep_policy
  GRANT (SELECT) ON (region_id)
  USING (
    region_id IN (
      SELECT region_id FROM user_region_mapping
      WHERE user_id = CURRENT_USER()
    )
  )
;

APPLY ROW ACCESS POLICY sales_rep_policy ON semantic_view;
```

### 12.2 Metadata Governance

**Versioning:**
- Track all measure definition changes
- Enable rollback to previous versions
- Audit trail of who changed what

**Access Control:**
- Read-only access for analysts
- Edit access for semantic engineers
- Admin access for governance
- Approval workflow for production changes

**Lineage Tracking:**
- Record measure dependencies
- Track column lineage to source
- Generate impact analysis on schema changes

### 12.3 Audit Logging

**Deployment Audits:**
```
Record:
  - When measures deployed
  - Who deployed them
  - Which measures changed
  - Parity scores before/after
  - Any failures or warnings
  
Query:
  SELECT * FROM _SEMANTIC_DEPLOYMENTS
  WHERE deployed_at > NOW() - INTERVAL 7 DAY
```

**Query Audits:**
```
Record:
  - Which queries use semantic views
  - Execution time and row count
  - Resource consumption
  - User and role
  
Enables:
  - Performance analysis
  - Usage tracking
  - Cost attribution
```

### 12.4 Semantic Change Tracking

**Question:** When measures change, what else is affected?

**Solution:** Change Impact Analysis

```sql
-- User changes [Total Sales] definition
INSERT INTO _SEMANTIC_CHANGE_LOG (...)
  VALUES ('measure_id', 'modified', ...);

-- System automatically identifies:
-- 1. Measures that depend on [Total Sales]
-- 2. Dashboards/reports that use those measures
-- 3. Scheduled jobs affected
-- 4. Generates impact report

SELECT measure_name, impacted_by
FROM _SEMANTIC_IMPACT_ANALYSIS
WHERE measure_id = 'Total Sales'
```

---

## 13. Risks & Constraints

### 13.1 Unsupported DAX Semantics

| Pattern | Status | Risk | Workaround |
|---------|--------|------|-----------|
| **EARLIER/EARLIEST** | ❌ | Row context not supported | Manual SQL or LLM fallback |
| **Complex Iterators** | ⚠️ | May produce wrong results | LLM + validation or refactor |
| **RANKX** | ⚠️ | Partial support; ranking may differ | Use ROW_NUMBER() or manual |
| **Dynamic Relationships** | ❌ | USERELATIONSHIP not supported | Defer to Phase 2 |
| **Implicit Context** | ⚠️ | Must infer context from query | May guess wrong; manual check |
| **Custom Functions** | ❌ | No M function execution | User must implement in SQL |

### 13.2 SQL Limitations

| Limitation | Impact | Workaround |
|-----------|--------|-----------|
| **Set-Based vs Row-Based** | Iterators hard to translate | Use window functions or subqueries |
| **No Row Context** | EARLIER equivalent unclear | LAG() function (different semantics) |
| **Null vs BLANK** | Semantic difference | Treat SQL NULL as DAX BLANK |
| **Type Coercion** | Different behavior | Explicit casting in all expressions |
| **Floating-Point Precision** | Results may differ | Accept ±0.01% tolerance |

### 13.3 Semantic Mismatch Risks

**Risk 1: Context Misinterpretation**
```
DAX ambiguous about context → translator chooses wrong interpretation
Result: Different aggregation target, wrong measure values
```

**Mitigation:**
- Validate against Fabric results
- Flag ambiguous expressions as warnings
- Require manual review

**Risk 2: Calendar Assumptions**
```
Translator assumes calendar year; model uses fiscal year
Result: YTD calculations use wrong boundaries
```

**Mitigation:**
- Auto-detect calendar type
- Store calendar assumptions in metadata
- Validate YTD boundaries against Fabric

**Risk 3: Floating-Point Divergence**
```
DAX and SQL use different precision → results differ by 0.0001%
Result: Validation fails even though semantically equivalent
```

**Mitigation:**
- Allow floating-point tolerance (0.1%)
- Round results to consistent precision
- Document precision expectations

### 13.4 Performance Tradeoffs

| Tradeoff | Upside | Downside |
|----------|--------|----------|
| **Materialize all measures** | Fast queries | High storage, slow refresh |
| **Compute on-demand** | Low storage | Slow queries, scalability issues |
| **Hybrid approach** | Balance | Complex logic |
| **Use LLM for translation** | Handles complex DAX | Expensive, non-deterministic |
| **Deterministic only** | Reproducible | Lose 15% of measures |

### 13.5 LLM Reliability Risks

**Risk 1: API Failure**
```
LLM service unavailable → deployment blocked
```

**Mitigation:**
- Cache translations offline
- Fallback to stubbed measure
- Alert user, manual intervention required

**Risk 2: Inconsistent Results**
```
Different LLM versions produce different SQL
Result: Deployment succeeds, query produces wrong results
```

**Mitigation:**
- Use same LLM version consistently
- Validate LLM output against Fabric
- Version LLM translations

**Risk 3: Hallucinations**
```
LLM generates plausible but wrong SQL
Result: Silent data corruption (hard to detect)
```

**Mitigation:**
- ALWAYS validate against Fabric results
- Block deployment if validation fails
- Manual review for high-complexity measures

---

## 14. Phased Roadmap

### Phase 1: Foundation (Weeks 1-8)

**Goal:** Establish semantic translation infrastructure and achieve 90% DAX coverage

**Deliverables:**

1. **Enhanced DAX Parser**
   - Semantic-aware AST generation
   - Context requirement tagging
   - Complexity tier classification
   - Estimated effort: 2 weeks

2. **Semantic Context Analyzer**
   - Understand aggregation targets
   - Identify filter context requirements
   - Generate semantic intent representation
   - Estimated effort: 2 weeks

3. **Tier 2-4 DAX Translation**
   - Implement SUMX/AVERAGEX/COUNTX (simple patterns)
   - Improve CALCULATE/FILTER/ALL handling
   - Complete time intelligence coverage
   - Estimated effort: 2 weeks

4. **Basic M Query Transformation**
   - Parse simple M queries
   - Translate SELECT/FILTER patterns
   - Generate equivalent SQL
   - Estimated effort: 1 week

5. **Parity Validation Framework**
   - Build comparison engine (Fabric vs Snowflake)
   - Implement test case generator
   - Create parity report generation
   - Estimated effort: 2 weeks

6. **Documentation & Training**
   - Architecture documentation
   - Implementation guide
   - Team training
   - Estimated effort: 1 week

**Success Criteria:**
- ✅ 90%+ of measures translate deterministically
- ✅ Parity score >98% on golden test set
- ✅ Zero data loss incidents
- ✅ <2% of measures require LLM fallback

### Phase 2: Core Semantic Parity (Weeks 9-16)

**Goal:** Achieve 99%+ semantic equivalence and production readiness

**Deliverables:**

1. **Advanced CALCULATE Translation**
   - Handle nested CALCULATE expressions
   - Implement complex context stacking
   - Support circular relationships
   - Estimated effort: 2 weeks

2. **Fiscal Calendar Support**
   - Auto-detect fiscal year calendars
   - Generate custom YTD window functions
   - Handle multi-calendar scenarios
   - Estimated effort: 1.5 weeks

3. **Advanced M Query Support**
   - Handle parameter binding
   - Preserve query folding
   - Support custom transformations (simple)
   - Estimated effort: 1.5 weeks

4. **Semantic Dependency Graph**
   - Build complete dependency model
   - Implement cycle detection
   - Generate topological ordering
   - Support impact analysis
   - Estimated effort: 2 weeks

5. **Comprehensive Validation**
   - Row-level parity testing
   - Context-aware validation
   - Relationship semantic testing
   - Regression framework
   - Estimated effort: 2 weeks

6. **Performance Optimization**
   - Implement translation caching
   - Parallelize translation (8 workers)
   - Incremental sync support
   - Measure materialization strategy
   - Estimated effort: 1.5 weeks

**Success Criteria:**
- ✅ 99%+ measures have parity score >99.9%
- ✅ <1% of measures require manual intervention
- ✅ Incremental sync <30 seconds for typical changes
- ✅ Production deployment template ready

### Phase 3: Advanced Semantic Runtime (Weeks 17-24)

**Goal:** Build sophisticated semantic execution runtime for complex scenarios

**Deliverables:**

1. **RANKX & Complex Iterators**
   - Implement RANKX translation
   - Handle complex nested iterators
   - Support EARLIER (simplified)
   - Estimated effort: 2 weeks

2. **Dynamic Relationship Support**
   - Implement USERELATIONSHIP translation
   - Support context-aware relationship switching
   - Handle complex join scenarios
   - Estimated effort: 2 weeks

3. **RLS Integration**
   - Auto-generate Snowflake RLS policies
   - Map Fabric RLS rules
   - Test RLS enforcement
   - Estimated effort: 1.5 weeks

4. **Metadata-Driven Execution**
   - Build measure versioning system
   - Implement change tracking
   - Create audit trail
   - Deploy metadata governance
   - Estimated effort: 1.5 weeks

5. **Advanced Validation**
   - Semantic equivalence proof system
   - Real-time parity monitoring
   - Automated regression detection
   - Performance impact analysis
   - Estimated effort: 1.5 weeks

6. **Enterprise Hardening**
   - Production deployment playbook
   - Disaster recovery procedures
   - Rollback strategies
   - Performance tuning guide
   - Estimated effort: 1 week

**Success Criteria:**
- ✅ 99.5%+ parity on all real-world models tested
- ✅ Zero data loss incidents
- ✅ <5% of measures require LLM fallback
- ✅ Enterprise-ready deployment procedures

### Phase 4: Enterprise Optimization (Weeks 25-32)

**Goal:** Scale to enterprise workloads, optimize for cost and performance

**Deliverables:**

1. **Scalability Enhancements**
   - Support 10,000+ measure models
   - Parallel deployment (batch processing)
   - Distributed translation (multi-node)
   - Estimated effort: 2 weeks

2. **Cost Optimization**
   - Intelligent materialization strategy
   - Dynamic table refresh scheduling
   - Query result caching
   - Storage optimization
   - Estimated effort: 2 weeks

3. **Advanced Monitoring**
   - Real-time parity monitoring
   - Query performance analytics
   - Cost attribution
   - Anomaly detection
   - Estimated effort: 1.5 weeks

4. **Multi-Tenant Support**
   - Separate workspace isolation
   - Cross-tenant dependency tracking
   - Shared semantic library
   - Estimated effort: 2 weeks

5. **Cortex Analyst Integration**
   - Auto-generate Cortex YAML
   - Semantic metadata for AI context
   - Natural language query optimization
   - Estimated effort: 1.5 weeks

6. **Reference Implementation**
   - End-to-end demo
   - Best practices guide
   - Performance benchmarks
   - ROI calculator
   - Estimated effort: 1 week

**Success Criteria:**
- ✅ Enterprise-scale model (5000+ measures) syncs in <5 minutes
- ✅ Cost per measure <$0.01/month
- ✅ Query response time within 10% of Fabric
- ✅ Production support playbook documented

---

## 15. Final Recommendations

### 15.1 Recommended Architecture Direction

**Primary Recommendation: Hybrid Deterministic + LLM-Fallback**

**Rationale:**
1. **Deterministic for 90%+:** Most DAX follows predictable patterns
2. **LLM for edge cases:** Handles complex logic reliably
3. **Validation critical:** Always compare Fabric vs Snowflake
4. **Reproducible:** Cached translations ensure consistency

**Why Not Alternatives:**

| Approach | Why Not |
|----------|---------|
| **Pure LLM** | Expensive, non-deterministic, hallucinations |
| **Pure Deterministic** | Can't handle complex DAX, users demand coverage |
| **Manual Translation** | Time-consuming, error-prone, defeats purpose |

### 15.2 Recommended Implementation Priorities

**Priority 1: Get Parity Validation Working**
```
Rationale:
  - Cannot trust translations without validation
  - Must prove semantic equivalence
  - Foundation for all future work
  
Timeline: Phase 1, Week 1-2
Impact: Enables confident deployment
```

**Priority 2: Expand Tier 1-3 Coverage**
```
Rationale:
  - 90% of measures are Tier 1-3
  - Deterministic translation is fast and reliable
  - Each tier adds 5-10% coverage
  
Timeline: Phase 1, Week 3-4
Impact: Eliminates most LLM calls
```

**Priority 3: Implement M Query Transformation**
```
Rationale:
  - Currently lost in translation
  - Relatively tractable (simple patterns)
  - Significant business value (preserves ETL logic)
  
Timeline: Phase 1, Week 5
Impact: Enables query logic portability
```

**Priority 4: Build Dependency Graph**
```
Rationale:
  - Enables correct materialization ordering
  - Detects cycles before deployment
  - Foundation for impact analysis
  
Timeline: Phase 2, Week 1-2
Impact: Production-ready reliability
```

**Priority 5: Fiscal Calendar Support**
```
Rationale:
  - 20% of enterprises use fiscal calendars
  - Time intelligence accuracy critical
  - Moderate complexity
  
Timeline: Phase 2, Week 3-4
Impact: Geographic/industry coverage
```

### 15.3 What Should NOT Be Attempted Initially

**Don't Attempt: Full EARLIER/Earliest Support**
```
Reason:
  - Requires DAX row context engine
  - 6+ weeks of engineering
  - ROI unclear
  
Better:
  - Handle 80% with LAG() function
  - Defer complex cases to Phase 2+
  - Let users request if needed
```

**Don't Attempt: Full M Query Language Support**
```
Reason:
  - M is Turing-complete
  - Custom functions, loops, etc.
  - Diminishing returns
  
Better:
  - Support 90% of patterns (SELECT, FILTER, TRANSFORM)
  - Defer custom functions to user implementation
  - Document unsupported patterns
```

**Don't Attempt: Multi-Cloud Support Initially**
```
Reason:
  - Adds complexity
  - Different SQL dialects
  - Can be added later
  
Better:
  - Focus on Snowflake (largest opportunity)
  - Abstract SQL generation for future cloud support
  - Plan for Databricks/BigQuery in Phase 2+
```

**Don't Attempt: Real-Time Sync**
```
Reason:
  - Complex infrastructure
  - High ongoing cost
  - Batch is usually sufficient
  
Better:
  - Batch sync (hourly, daily)
  - Event-driven sync (on model change)
  - Real-time in Phase 3+ if needed
```

### 15.4 Realistic Parity Expectations

**Achievable:**
- ✅ 99%+ semantic equivalence on well-formed models
- ✅ 0% data loss (with validation)
- ✅ <0.1% result deviation for aggregations
- ✅ 100% relationship semantics preserved

**Not Achievable:**
- ❌ 100% coverage (some DAX patterns not translatable)
- ❌ Perfect performance parity (different query engines)
- ❌ Pixel-perfect UI equivalence (different platforms)

**Realistic Metrics (by end of Phase 2):**
- 99%+ of measures achieve parity
- 1%+ of measures require manual review/refactoring
- 0% silent failures (all mismatches detected)
- <1% of queries require troubleshooting

### 15.5 Success Metrics & Go/No-Go Criteria

**Phase 1 Go/No-Go:**
- [ ] 90% of test measures translate successfully
- [ ] Parity validation engine working
- [ ] Zero data loss incidents
- [ ] <5% of engineering time spent on issues

**Phase 2 Go/No-Go:**
- [ ] 99% of measures achieve parity
- [ ] Dependency graph working correctly
- [ ] No regressions detected
- [ ] Production deployment playbook complete

**Phase 3 Go/No-Go:**
- [ ] 99.5%+ parity on enterprise models
- [ ] Scaling tested to 10,000+ measures
- [ ] RLS integration working
- [ ] Customer pilots successful

**Phase 4 Go/No-Go:**
- [ ] Enterprise deployment capability verified
- [ ] Cost per measure <$0.01/month
- [ ] 99.9% uptime verified
- [ ] Reference implementation complete

---

## Conclusion

This initiative represents a **significant undertaking** (6-8 months, 4-5 engineers) but with **clear business value** and **achievable technical scope**.

**Key Success Factors:**
1. **Parity validation first:** Without it, everything else is guesswork
2. **Deterministic translation for common patterns:** 90% coverage without LLM
3. **Clear failure modes:** Better to block deployment than silently corrupt data
4. **Incremental delivery:** Get Phases 1-2 to customers before Phase 3-4

**Recommended Next Step:**
- Approve Phase 1 scope and budget
- Assemble team (DAX engineer, SQL engineer, validation engineer)
- Begin detailed sprint planning
- Target Phase 1 completion: 8 weeks

---

**Document Author:** Architecture Review  
**Approval:** TBD  
**Version History:**
- v1.0 - Initial draft (May 11, 2026)
