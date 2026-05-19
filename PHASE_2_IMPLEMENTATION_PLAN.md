# PHASE 2 IMPLEMENTATION PLAN
## Advanced Semantic Parity & Complex Translation

**Initiative:** Fabric Semantic Logic Preservation & Snowflake Semantic Execution  
**Phase:** Phase 2 — Advanced Semantic Parity  
**Duration:** 12 weeks (3 months)  
**Team Size:** 4.5 FTE  
**Created:** May 11, 2026

---

## 1. PHASE OBJECTIVE

### Business Objective

Phase 2 extends Semabridge from covering 90% of real-world DAX measures (Phase 1) to 98%+ coverage by implementing support for complex translation patterns that are deterministic but require sophisticated semantic analysis. This phase unlocks support for:

- **Complex CALCULATE expressions** with multiple filter contexts
- **Row context evaluation** (EARLIER, EARLIEST functions)
- **Iterator functions** (SUMX, AVERAGEX, RANKX with complex logic)
- **Dynamic cardinality adjustments** via USERELATIONSHIP
- **Semantic context preservation** across nested function calls

### Business Impact

- **Semantic equivalence increases** from 90% to 98%+
- **Customer models with complex DAX** now sync instead of failing
- **LLM fallback usage drops** from 10% to <2%
- **Sync reliability** improves from 95% to 99.5% (fewer exceptions)
- **Support tickets** related to "unsupported measure pattern" decrease 85%

### Technical Objective

Implement advanced DAX translation engine capable of:

1. **Multi-context filter reasoning** — Understand how CALCULATE stacks filter contexts
2. **Row context synthesis** — Convert row-iterating patterns to window functions
3. **Iterator transformation** — Translate SUMX/AVERAGEX into CTEs or window functions
4. **Semantic intent inference** — Understand implicit business logic (e.g., "ratio of current to prior period")
5. **Contextual correctness validation** — Prove semantic equivalence via symbolic execution

### Why This Phase Exists

**Phase 1 Gap Analysis:** After Phase 1 delivery, real customer models expose that:
- 5-8% of measures use CALCULATE with complex nested filters
- 2-3% use row context (EARLIER, EARLIER OLDEST)
- 1-2% use iterator patterns with complex filter contexts

These patterns are **deterministically translatable** but require:
- **Semantic annotation** of function parameters
- **Filter context tracking** across nested calls
- **Row context inference** using window function equivalents
- **Correctness proofs** via symbolic execution

Attempting LLM translation of these patterns creates unacceptable risk because the LLM cannot prove semantic equivalence. Phase 2 implements deterministic translation strategies for these patterns, eliminating LLM dependency for real-world measures.

---

## 2. SCOPE BOUNDARY

### Explicitly IN Scope

**Complex DAX Patterns (Deterministic):**
- ✅ CALCULATE with multiple nested filter contexts
  - `CALCULATE([Sales], FILTER(Dates, Dates[Year]=2025), ALL(Products))`
  - `CALCULATE([Sales], REMOVEFILTERS(Category), ADDFILTERS(NewCategory))`
  - Nested CALCULATE + FILTER combinations (up to 3-4 levels)

- ✅ Row context functions (deterministic equivalents)
  - `EARLIER(Column, [offset])` → Window function with LAG/LEAD
  - `EARLIEST(Column)` → Window function with FIRST_VALUE
  - Row context within SUMX/AVERAGEX iterators

- ✅ Iterator functions with filter contexts
  - `SUMX(Table, Condition, Expression)` → CTE with aggregation
  - `AVERAGEX(Table, Expression)` → CTE with aggregation
  - `RANKX(Table, Expression, [Order])` → Window function RANK/ROW_NUMBER

- ✅ Dynamic relationship functions
  - `USERELATIONSHIP(Column1, Column2)` → Explicit JOIN override (in Snowflake context)
  - Relationship direction changes via USERELATIONSHIP

- ✅ Context transition functions
  - `ADDCOLUMNS(Table, NewCol, Expr)` → CTE with computed columns
  - `SUMMARIZECOLUMNS(...)` → GROUP BY equivalent

- ✅ Complex time intelligence
  - `TOTALYTD(Expr, DateCol, [FilterExpr])` → Window function with date range
  - `SAMEPERIODLASTYEAR(DateCol)` → Window OVER PARTITION BY with DATE_TRUNC
  - `DATEADD(DateCol, Offset, Unit)` with variable offsets

**Advanced Validation:**
- ✅ Symbolic execution proof of semantic equivalence
- ✅ Correctness validation with edge cases (NULL handling, empty sets, cardinality changes)
- ✅ Filter context tracing (what filters apply at each step)
- ✅ Row context tracking (which rows are in scope)

**Parity Testing:**
- ✅ Complex DAX test suite (500+ test cases)
- ✅ Golden dataset with real customer models
- ✅ Symbolic execution correctness proofs
- ✅ Performance comparison (Fabric vs Snowflake)
- ✅ Regression testing (Phase 1 patterns still work)

**LLM Optimization:**
- ✅ Fallback LLM for <2% of edge cases
- ✅ Caching layer for recurring patterns
- ✅ Human review workflow for LLM translations
- ✅ Cost optimization (reduce API calls 90%)

### Explicitly OUT of Scope (Defer to Phase 3+)

**Runtime Semantics:**
- ❌ RLS (Row-Level Security) implementation
- ❌ Dynamic measure definitions (calculated columns changing at runtime)
- ❌ Bidirectional sync (Snowflake → Fabric)

**Advanced Language Features:**
- ❌ M Query translation (defer to Phase 1 extension)
- ❌ Complex custom functions with state management
- ❌ Power Query step-by-step transformation preservation

**Enterprise Features:**
- ❌ Audit trail for semantic transformations
- ❌ Cost attribution per measure
- ❌ Multi-tenant semantic isolation
- ❌ Cortex AI native search integration (Phase 3)

**Optimization:**
- ❌ Translation parallelization
- ❌ LLM model fine-tuning
- ❌ GPU-accelerated symbolic execution
- ❌ Distributed measure sync

### Dependencies on Previous Phases

**REQUIRED FROM PHASE 1:**
- ✅ SemanticAstParser (enhanced with semantic annotations)
- ✅ SemanticContextAnalyzer (understanding measure intent)
- ✅ DaxTranslationEngine (Tier 2-4 patterns working)
- ✅ ParityValidationEngine (basic correctness checks)
- ✅ SemanticDependencyGraph (measure dependency tracking)
- ✅ MQueryTransformer (simple patterns)

**EXTENDS FROM PHASE 1:**
- Existing SemanticAstParser → Enhanced for complex CALCULATE tracking
- ParityValidationEngine → Extended with symbolic execution
- DaxTranslationEngine → New tier translators (Complex CALCULATE, Iterators, Row Context)
- MeasureClassifier → Enhanced with pattern confidence scoring

**NOT BREAKING:**
- Phase 1 tests remain passing (backward compatibility)
- Phase 1 performance baselines (no regression)
- Phase 1 semantic models (ISMLModel interface)

---

## 3. CORE DELIVERABLES

### 3.1 Advanced DAX Translation Engine

**Purpose:** Translate complex DAX patterns (CALCULATE depth, row context, iterators) into semantically equivalent Snowflake SQL

**Deliverables:**
1. **ComplexCalculateTranslator** (450 lines)
   - Parse nested CALCULATE with multiple filters
   - Track filter context stack
   - Generate SQL with CTEs for context isolation
   - Handle REMOVEFILTERS, ADDFILTERS, ALL combinations

2. **RowContextTranslator** (350 lines)
   - Detect EARLIER/EARLIEST function usage
   - Synthesize window functions with ROW_NUMBER, LAG, FIRST_VALUE
   - Handle row context within iterators
   - Preserve semantic ordering

3. **IteratorTranslator** (400 lines)
   - Transform SUMX, AVERAGEX, RANKX patterns
   - Generate CTEs for iteration
   - Apply filter contexts to iterator expressions
   - Handle nested iterators (SUMX within AVERAGEX)

4. **RelationshipOverrideTranslator** (200 lines)
   - Handle USERELATIONSHIP overrides
   - Generate explicit JOINs instead of implicit relationships
   - Track relationship cardinality changes

5. **ComplexTimeIntelligenceTranslator** (300 lines)
   - Advanced YTD, QTD, MTD patterns
   - DATEADD with variable offsets
   - Seasonal comparisons (year-over-year, quarter-over-quarter)

**Expected Output:**
- 95%+ of Phase 2 DAX patterns translate without LLM fallback
- <2% require LLM assistance
- SQL generation maintains semantic correctness

### 3.2 Symbolic Execution Engine

**Purpose:** Prove semantic equivalence between Fabric DAX and Snowflake SQL by symbolic execution

**Deliverables:**
1. **SymbolicExecutor** (600 lines)
   - Execute DAX expressions symbolically (without real data)
   - Track value ranges and NULL handling
   - Detect semantic divergence points
   - Generate correctness proof or counterexample

2. **EquivalenceValidator** (400 lines)
   - Compare symbolic execution of DAX vs SQL
   - Identify semantic differences
   - Flag potential data loss or type coercion
   - Generate detailed divergence reports

3. **EdgeCaseTestGenerator** (300 lines)
   - Automatically generate test cases for edge cases
   - NULL handling, empty sets, cardinality changes
   - Extreme values (MIN/MAX for data types)
   - Division by zero, aggregate edge cases

**Expected Output:**
- Formal proof of correctness for 98%+ of measures
- Automated detection of semantic mismatches
- Edge case coverage (1000+ automatically generated tests)

### 3.3 Filter Context Analyzer

**Purpose:** Track filter context propagation through nested CALCULATE, SUMMARIZECOLUMNS, and iterator patterns

**Deliverables:**
1. **FilterContextTracker** (350 lines)
   - Build filter context stack during translation
   - Track which filters apply at each level
   - Detect filter context conflicts
   - Generate filter application order documentation

2. **ContextConflictDetector** (200 lines)
   - Identify REMOVEFILTERS/ADDFILTERS conflicts
   - Detect circular filter contexts (impossible to satisfy)
   - Flag ambiguous filter semantics
   - Generate warnings for manual review

3. **ContextPropagationValidator** (250 lines)
   - Verify filter propagation through nested functions
   - Check that filters apply at correct semantic level
   - Validate CALCULATE nesting depth
   - Ensure semantic correctness of filter chains

**Expected Output:**
- Complete filter context documentation for each measure
- Automatic detection of filter semantics errors
- Visual trace of how filters apply

### 3.4 Advanced Parity Testing Framework

**Purpose:** Comprehensive validation that complex DAX measures produce equivalent results

**Deliverables:**
1. **GoldenDatasetExpander** (200 lines)
   - Expand Phase 1 golden dataset (250 measures) to 500+ complex measures
   - Include real customer models with CALCULATE depth, iterators, row context
   - Stratified by complexity tier and pattern type
   - Snowflake and Fabric side-by-side comparison

2. **ParityTestRunner** (300 lines)
   - Execute measures on both Fabric and Snowflake
   - Compare results row-by-row, aggregate-level
   - Measure precision (float comparison with tolerance)
   - Flag even micro-level discrepancies

3. **SymbolicParityChecker** (250 lines)
   - Combine actual data results + symbolic execution
   - Detect where semantic differences manifest
   - Correlate failures to specific translation patterns
   - Generate root cause analysis

4. **RegressionTestSuite** (200 lines)
   - Ensure Phase 1 patterns remain 100% correct
   - Automatic detection of regressions
   - Version-to-version comparison
   - Performance regression detection

**Expected Output:**
- 500+ parity test cases passing
- 98%+ semantic equivalence measured
- Zero regressions from Phase 1
- Documented edge cases and handling

### 3.5 LLM Optimization Framework

**Purpose:** For the <2% of measures requiring LLM, provide caching, validation, and cost optimization

**Deliverables:**
1. **LLMFallbackCache** (200 lines)
   - Cache LLM translations by pattern hash
   - Detect recurring patterns
   - Reduce LLM API calls by 90%
   - Cost tracking per pattern

2. **LLMValidationGate** (150 lines)
   - Validate LLM output before accepting
   - Check for semantic preservation
   - Flag suspicious translations for human review
   - Maintain human review queue

3. **HumanReviewWorkflow** (200 lines)
   - Queue suspicious LLM translations
   - Provide side-by-side Fabric/Snowflake results
   - Allow engineers to approve/reject/refine
   - Learn from human decisions

4. **LLMCostOptimizer** (100 lines)
   - Track cost per measure translation
   - Identify expensive-to-translate patterns
   - Suggest optimization opportunities
   - Cost forecasting for future models

**Expected Output:**
- LLM API calls reduced by 90%
- 100% of LLM translations validated before deployment
- <$1 cost per complex measure translation
- Human review time <5 minutes per translation

### 3.6 Complex Pattern Documentation

**Purpose:** Maintain comprehensive documentation of supported complex DAX patterns

**Deliverables:**
1. **Complex Pattern Catalog** (500 lines)
   - Document all supported CALCULATE variations
   - Row context pattern library
   - Iterator pattern documentation
   - Real code examples for each pattern

2. **Translation Strategy Guide** (300 lines)
   - Explain translation approach for each pattern type
   - CTE vs window function decisions
   - Edge case handling strategies
   - Performance implications

3. **Troubleshooting Guide** (200 lines)
   - Common errors and fixes
   - Pattern matching guidance
   - Performance tuning recommendations
   - When to use LLM fallback

**Expected Output:**
- 1000+ page documentation
- Engineers can self-serve pattern translation
- Clear decision trees for pattern handling
- Future maintainability

---

## 4. ARCHITECTURE ADDITIONS

### 4.1 New Modules & Components

```
src/semabridge/converter/
├── complex_calculate_translator.py      [NEW] 450 lines
├── row_context_translator.py             [NEW] 350 lines
├── iterator_translator.py                [NEW] 400 lines
├── relationship_override_translator.py  [NEW] 200 lines
├── complex_time_intelligence.py         [NEW] 300 lines
├── symbolic_executor.py                 [NEW] 600 lines
├── equivalence_validator.py             [NEW] 400 lines
├── edge_case_generator.py               [NEW] 300 lines
├── filter_context_tracker.py            [NEW] 350 lines
├── context_conflict_detector.py         [NEW] 200 lines
└── context_propagation_validator.py     [NEW] 250 lines

src/semabridge/validation/
├── advanced_parity_validator.py         [NEW] 300 lines
├── golden_dataset_expander.py           [NEW] 200 lines
└── regression_test_suite.py             [NEW] 200 lines

src/semabridge/llm_optimization/
├── llm_fallback_cache.py                [NEW] 200 lines
├── llm_validation_gate.py               [NEW] 150 lines
├── human_review_workflow.py             [NEW] 200 lines
└── llm_cost_optimizer.py                [NEW] 100 lines

docs/patterns/
├── complex_calculate_patterns.md        [NEW] 300 lines
├── row_context_patterns.md              [NEW] 200 lines
├── iterator_patterns.md                 [NEW] 200 lines
└── troubleshooting_guide.md             [NEW] 200 lines
```

**Total New Code:** ~4,450 lines of implementation code + 900 lines of documentation

### 4.2 Data Structure Additions

```python
# Enhanced semantic intent model
class ComplexCalculateIntent:
    """Semantic intent for complex CALCULATE expressions"""
    base_measure: str
    filter_contexts: List[FilterContext]  # Stack of nested filters
    removed_filters: List[str]  # What filters are removed
    added_filters: List[str]    # What filters are added
    context_depth: int
    is_bidirectional: bool      # ALL() or unidirectional filters

class FilterContext:
    """Represents a single filter context in the stack"""
    filter_table: str
    filter_column: str
    filter_values: Optional[List[Any]]  # If literal values
    filter_expression: Optional[str]     # If complex expression
    propagation_mode: Literal["include", "exclude", "replace"]
    semantic_intent: str        # Why this filter (user intent)

class RowContextIntent:
    """Semantic intent for EARLIER/EARLIEST patterns"""
    base_expression: str
    offset: Optional[int]       # For EARLIER
    row_context_type: Literal["current", "earlier", "earliest"]
    ordering_column: str        # What defines row order
    partition_by: Optional[str] # Window partition

class IteratorIntent:
    """Semantic intent for SUMX/AVERAGEX/RANKX patterns"""
    iterator_function: Literal["SUMX", "AVERAGEX", "RANKX"]
    table: str
    expression: str
    filter_contexts: List[FilterContext]
    nested_iterator: Optional["IteratorIntent"]  # If nested
    output_type: str

class SymbolicValue:
    """Represents a value during symbolic execution"""
    python_type: type
    nullable: bool
    min_value: Optional[Any]
    max_value: Optional[Any]
    possible_values: Optional[Set[Any]]
    symbolic_expression: str    # How value was computed

class EquivalenceProof:
    """Proof of equivalence between DAX and SQL"""
    dax_pattern: str
    sql_pattern: str
    is_equivalent: bool
    test_cases_passed: int
    test_cases_failed: int
    counterexample: Optional[Dict[str, Any]]  # If not equivalent
    symbolic_trace: List[Dict[str, Any]]

class FilterContextTrace:
    """Complete trace of filter application through DAX expression"""
    steps: List[FilterStep]
    final_filters: List[str]
    conflicts_detected: List[str]

class FilterStep:
    """Single step in filter application trace"""
    line_number: int
    function: str               # CALCULATE, FILTER, etc
    filters_applied: List[str]
    filters_removed: List[str]
    context_depth_before: int
    context_depth_after: int
```

### 4.3 Integration Points

**With Phase 1 Modules:**
- **SemanticAstParser** extends: Enhanced to track CALCULATE nesting depth, filter contexts
- **DaxTranslationEngine** extends: New tier translators registered (Complex CALCULATE → tier 4.5, Iterators → tier 4.8)
- **ParityValidationEngine** extends: Now includes symbolic execution + edge case validation
- **MeasureClassifier** extends: New classification for complex patterns

**Data Flow:**
```
Input DAX
    ↓
[Phase 1] SemanticAstParser (with semantic annotations)
    ↓
[Phase 2] NEW ComplexCalculateAnalyzer
[Phase 2] NEW RowContextAnalyzer
[Phase 2] NEW IteratorAnalyzer
    ↓
[Phase 2] NEW SymbolicExecutor (proof of correctness)
    ↓
[Phase 1] DaxTranslationEngine (now handles complex patterns)
    ↓
[Phase 2] NEW FilterContextTracker (trace filters)
    ↓
Generated SQL
    ↓
[Phase 2] NEW AdvancedParityValidator (symbolic + data comparison)
    ↓
[Phase 1] SchemaCompatibilityValidator
    ↓
Snowflake Emission
```

### 4.4 API Changes (Backward Compatible)

**DaxTranslationEngine.translate()** signature unchanged:
```python
def translate(
    dax_expr: str,
    context: TranslationContext,
    fallback_to_llm: bool = False  # NEW parameter
) -> TranslationResult:
    # Now returns proof of correctness if available
    # Includes symbolic execution trace
```

**New classes added to existing interfaces:**
```python
class TranslationResult:  # EXTENDED
    # Phase 1 fields remain unchanged
    correctness_proof: Optional[EquivalenceProof]  # NEW
    filter_context_trace: Optional[FilterContextTrace]  # NEW
    semantic_intent: Optional[ComplexCalculateIntent]  # NEW
    symbolic_execution_trace: Optional[List[Dict]]  # NEW
```

---

## 5. ENGINEERING TASK BREAKDOWN

### 5.1 Module Implementation Tasks (Priority Order)

#### PHASE 2.1: Advanced Filter Context Tracking (Weeks 1-2)

**T2.1.1: FilterContextTracker Implementation** [4 days]
- Prerequisite: Phase 1 SemanticAstParser complete
- Track CALCULATE nesting depth (max 4-5 levels realistic)
- Implement filter context stack management
- Handle REMOVEFILTERS, ADDFILTERS, ALL
- Complexity: Medium
- Owner: DAX Engineer (1x)
- Review: Architecture review + 2 test engineers

**T2.1.2: ContextConflictDetector Implementation** [3 days]
- Detect impossible filter combinations
- Flag circular dependencies
- Identify ambiguous semantics
- Complexity: Medium
- Owner: DAX Engineer (1x)
- Review: Semantic consistency check

**T2.1.3: ContextPropagationValidator** [2 days]
- Validate filter propagation correctness
- Check semantic level of filters
- Ensure CALCULATE nesting correct
- Complexity: Low-Medium
- Owner: DAX Engineer (0.5x)

**T2.1.4: FilterContextTracker Unit Tests** [3 days]
- 100+ test cases for filter tracking
- Edge cases: nested CALCULATE, REMOVEFILTERS combinations
- Symbolic execution of filter stacks
- Owner: QA Engineer (1x)

**Dependencies:** Phase 1 SemanticAstParser  
**Deliverable:** FilterContextTracker + ContextConflictDetector working  
**Success Criteria:** All 100+ tests pass, zero regressions from Phase 1

---

#### PHASE 2.2: Symbolic Execution Foundation (Weeks 2-3)

**T2.2.1: SymbolicExecutor Core** [4 days]
- Build symbolic value representation
- Implement symbolic arithmetic operations
- Handle NULL propagation
- Track value ranges
- Complexity: High
- Owner: DAX Engineer (1.5x)
- Review: Architecture review, correctness proof

**T2.2.2: EquivalenceValidator** [3 days]
- Compare symbolic execution of DAX vs SQL
- Generate counterexamples for mismatches
- Document equivalence proofs
- Complexity: High
- Owner: DAX Engineer (1.5x)

**T2.2.3: EdgeCaseTestGenerator** [2 days]
- Auto-generate test cases from symbolic execution
- NULL handling, extreme values, cardinality changes
- Complexity: Medium
- Owner: QA Engineer (0.5x)

**T2.2.4: Symbolic Execution Unit Tests** [3 days]
- 150+ test cases for symbolic execution
- Edge case coverage
- Correctness proof validation
- Owner: QA Engineer (1x)

**Dependencies:** Phase 1 DaxTranslationEngine, T2.1 FilterContextTracker  
**Deliverable:** SymbolicExecutor + EquivalenceValidator working  
**Success Criteria:** 98%+ correctness proofs generated, edge case detection working

---

#### PHASE 2.3: Complex CALCULATE Translation (Weeks 3-5)

**T2.3.1: ComplexCalculateTranslator** [5 days]
- Parse nested CALCULATE with multiple filters
- Generate SQL CTEs for context isolation
- Handle filter context stacking
- Complexity: High
- Owner: SQL Engineer (1.5x) + DAX Engineer (0.5x)
- Review: SQL correctness review, semantics review

**T2.3.2: Relationship Override Handling (USERELATIONSHIP)** [2 days]
- Detect USERELATIONSHIP calls
- Generate explicit JOINs
- Track cardinality changes
- Complexity: Medium
- Owner: SQL Engineer (1x)

**T2.3.3: ComplexTimeIntelligenceTranslator** [3 days]
- Advanced YTD, QTD, MTD patterns
- DATEADD with variable offsets
- Seasonal comparisons
- Complexity: Medium-High
- Owner: SQL Engineer (1x)

**T2.3.4: ComplexCalculateTranslator Tests** [4 days]
- 200+ test cases for CALCULATE patterns
- Real customer models (from golden dataset)
- Edge cases: nested depth, conflicts
- Owner: QA Engineer (1x)

**T2.3.5: Integration with Phase 1 Engine** [2 days]
- Register ComplexCalculateTranslator with DaxTranslationEngine
- Ensure backward compatibility
- Fallback to LLM for unsupported patterns
- Complexity: Low-Medium
- Owner: Infrastructure Engineer (0.5x)

**Dependencies:** T2.2 SymbolicExecutor, T2.1 FilterContextTracker  
**Deliverable:** 95%+ of complex CALCULATE patterns translate  
**Success Criteria:** 200+ test cases pass, zero Phase 1 regressions, <5min per measure

---

#### PHASE 2.4: Row Context Translation (Weeks 4-6)

**T2.4.1: RowContextTranslator** [4 days]
- Detect EARLIER/EARLIEST usage
- Generate window functions (LAG, LEAD, FIRST_VALUE)
- Handle row context ordering
- Preserve semantic correctness
- Complexity: High
- Owner: SQL Engineer (1.5x) + DAX Engineer (0.5x)

**T2.4.2: RowContextTranslator Tests** [3 days]
- 150+ test cases for row context patterns
- Edge cases: NULL handling, empty windows
- Symbolic execution proofs
- Owner: QA Engineer (1x)

**T2.4.3: Nested Iterator + Row Context** [2 days]
- Handle EARLIER within SUMX/AVERAGEX
- Complex ordering + aggregation scenarios
- Complexity: High
- Owner: SQL Engineer (1x)

**Dependencies:** T2.2 SymbolicExecutor, T2.3 ComplexCalculateTranslator  
**Deliverable:** EARLIER/EARLIEST patterns translate correctly  
**Success Criteria:** 150+ tests pass, <0.1% semantic deviation from Fabric

---

#### PHASE 2.5: Iterator Translation (Weeks 5-7)

**T2.5.1: IteratorTranslator Base** [4 days]
- Transform SUMX, AVERAGEX, RANKX patterns
- Generate CTEs for iteration simulation
- Handle filter contexts within iterators
- Complexity: High
- Owner: SQL Engineer (1.5x) + DAX Engineer (0.5x)

**T2.5.2: Complex Iterator Patterns** [3 days]
- Nested iterators (SUMX within AVERAGEX)
- Iterators with row context
- Cardinality changes in iteration
- Complexity: High
- Owner: SQL Engineer (1x)

**T2.5.3: IteratorTranslator Tests** [3 days]
- 150+ test cases
- Real patterns from customer models
- Edge cases: empty tables, NULL aggregation
- Owner: QA Engineer (1x)

**T2.5.4: Performance Optimization** [2 days]
- Ensure CTEs don't explode performance
- Query plan optimization
- Complexity: Medium-High
- Owner: SQL Engineer (0.5x)

**Dependencies:** T2.2 SymbolicExecutor, T2.4 RowContextTranslator  
**Deliverable:** SUMX/AVERAGEX/RANKX patterns translate  
**Success Criteria:** 150+ tests pass, performance <5min per complex iterator

---

#### PHASE 2.6: Advanced Parity Testing (Weeks 6-8)

**T2.6.1: GoldenDatasetExpander** [3 days]
- Expand Phase 1 dataset (250 measures) to 500+ complex
- Real customer models (stratified by pattern type)
- Snowflake/Fabric side-by-side comparison
- Complexity: Medium
- Owner: QA Engineer (1x) + Infrastructure (0.5x)

**T2.6.2: ParityTestRunner** [3 days]
- Execute measures on both platforms
- Row-by-row, aggregate comparison
- Precision tolerance handling
- Complexity: Medium-High
- Owner: QA Engineer (1x)

**T2.6.3: SymbolicParityChecker** [2 days]
- Combine actual + symbolic results
- Detect semantic divergence
- Root cause analysis
- Complexity: Medium
- Owner: QA Engineer (0.5x)

**T2.6.4: RegressionTestSuite** [2 days]
- Ensure Phase 1 patterns still 100% correct
- Automatic regression detection
- Performance regression detection
- Complexity: Low-Medium
- Owner: Infrastructure Engineer (0.5x)

**T2.6.5: Parity Testing Execution** [4 days]
- Run full parity suite on golden dataset
- Fix failures, iterate
- Document edge cases
- Complexity: Medium
- Owner: QA Engineer (1x)

**Dependencies:** All T2.3-T2.5, T2.1-T2.2  
**Deliverable:** 500+ measures passing parity validation  
**Success Criteria:** 98%+ semantic equivalence, zero Phase 1 regressions

---

#### PHASE 2.7: LLM Optimization (Weeks 7-8)

**T2.7.1: LLMFallbackCache** [2 days]
- Cache LLM translations by pattern hash
- Reduce API calls by 90%
- Cost tracking
- Complexity: Low-Medium
- Owner: Infrastructure Engineer (0.5x)

**T2.7.2: LLMValidationGate** [2 days]
- Validate LLM output before accepting
- Flag suspicious translations
- Human review queue
- Complexity: Low-Medium
- Owner: Infrastructure Engineer (0.5x)

**T2.7.3: HumanReviewWorkflow** [2 days]
- Queue management system
- Approval/rejection/refinement UX
- Learning system for future improvements
- Complexity: Medium
- Owner: Infrastructure Engineer (1x)

**T2.7.4: LLMCostOptimizer** [1 day]
- Track cost per measure
- Cost forecasting
- Optimization recommendations
- Complexity: Low
- Owner: Infrastructure Engineer (0.5x)

**Dependencies:** T2.3-T2.5 (measures for <2% LLM fallback)  
**Deliverable:** LLM optimization framework operational  
**Success Criteria:** 90% API call reduction, <$1/measure cost

---

#### PHASE 2.8: Documentation & Pattern Library (Weeks 8-12)

**T2.8.1: Complex Pattern Catalog** [4 days]
- Document all CALCULATE variations
- Row context pattern library
- Iterator patterns
- Real code examples
- Complexity: Low-Medium
- Owner: Technical Writer (1x)

**T2.8.2: Translation Strategy Guide** [3 days]
- Explain translation approach per pattern
- CTE vs window function decisions
- Edge case handling
- Performance implications
- Complexity: Low-Medium
- Owner: Technical Writer + SQL Engineer (0.5x)

**T2.8.3: Troubleshooting Guide** [2 days]
- Common errors and fixes
- Pattern matching guidance
- Performance tuning
- LLM fallback guidance
- Complexity: Low
- Owner: Technical Writer (0.5x)

**T2.8.4: Internals Documentation** [3 days]
- Architecture of symbolic executor
- Filter context tracking algorithm
- Iterator translation strategy
- Complexity: Medium
- Owner: DAX Engineer (1x) + Technical Writer (0.5x)

**Dependencies:** All T2.1-T2.7 complete  
**Deliverable:** 1000+ pages of documentation  
**Success Criteria:** Engineers can self-serve pattern translation

---

### 5.2 Task Sequencing & Critical Path

```
WEEK 1-2: T2.1 (FilterContextTracker)
├─ T2.1.1: Core implementation [4d]
├─ T2.1.2: Conflict detector [3d]
├─ T2.1.3: Propagation validator [2d]
└─ T2.1.4: Unit tests [3d]
     ↓
WEEK 2-3: T2.2 (SymbolicExecutor) [Parallel with T2.1.4]
├─ T2.2.1: Core symbolic execution [4d]
├─ T2.2.2: Equivalence validator [3d]
├─ T2.2.3: Edge case generator [2d]
└─ T2.2.4: Unit tests [3d]
     ↓
WEEK 3-5: T2.3 (ComplexCalculateTranslator) [Depends on T2.1, T2.2]
├─ T2.3.1: Core implementation [5d]
├─ T2.3.2: USERELATIONSHIP [2d]
├─ T2.3.3: TimeIntelligence [3d]
├─ T2.3.4: Tests [4d]
└─ T2.3.5: Integration [2d]
     ↓
WEEK 4-6: T2.4 (RowContextTranslator) [Parallel with T2.3]
├─ T2.4.1: Core implementation [4d]
├─ T2.4.2: Tests [3d]
└─ T2.4.3: Nested patterns [2d]
     ↓
WEEK 5-7: T2.5 (IteratorTranslator) [Depends on T2.4]
├─ T2.5.1: Base implementation [4d]
├─ T2.5.2: Complex patterns [3d]
├─ T2.5.3: Tests [3d]
└─ T2.5.4: Performance opt [2d]
     ↓
WEEK 6-8: T2.6 (Advanced Parity) [Depends on T2.3-T2.5]
├─ T2.6.1: Dataset expansion [3d]
├─ T2.6.2: Test runner [3d]
├─ T2.6.3: Parity checker [2d]
├─ T2.6.4: Regression suite [2d]
└─ T2.6.5: Execution [4d]
     ↓
WEEK 7-8: T2.7 (LLM Optimization)
├─ T2.7.1: Cache [2d]
├─ T2.7.2: Validation gate [2d]
├─ T2.7.3: Review workflow [2d]
└─ T2.7.4: Cost optimizer [1d]
     ↓
WEEK 8-12: T2.8 (Documentation)
├─ T2.8.1: Pattern catalog [4d]
├─ T2.8.2: Strategy guide [3d]
├─ T2.8.3: Troubleshooting [2d]
└─ T2.8.4: Internals [3d]
```

**Critical Path:** T2.1 → T2.2 → T2.3 → T2.6 (foundation → symbolic → translation → validation)

**Parallelizable:** T2.4 and T2.5 can run parallel to T2.3 (after T2.2 complete)

**Weeks 1-4:** Foundation (must be sequential)  
**Weeks 4-7:** Translation modules (partial parallelization)  
**Weeks 6-8:** Validation (depends on all translators)  
**Weeks 8-12:** Documentation (can start after T2.5, accelerates with parallel effort)

---

## 6. DATA STRUCTURES & CONTRACTS

### 6.1 Semantic Intent Models (New)

```python
# src/semabridge/models/phase_2_semantic_intent.py

from typing import List, Optional, Dict, Any, Literal, Set
from dataclasses import dataclass, field
from enum import Enum

class FilterMode(str, Enum):
    """How a filter is applied"""
    INCLUDE = "include"       # Normal filter (WHERE clause)
    EXCLUDE = "exclude"       # NOT filter
    REPLACE = "replace"       # ADDFILTERS replaces previous
    REMOVE = "remove"         # REMOVEFILTERS/ALL removes

@dataclass
class FilterContext:
    """Single filter context in CALCULATE stack"""
    filter_table: str
    filter_column: str
    filter_values: Optional[List[Any]] = None  # Literal values
    filter_expression: Optional[str] = None     # Complex expression
    propagation_mode: FilterMode = FilterMode.INCLUDE
    semantic_intent: str = ""    # Why this filter (user intent)
    is_bidirectional: bool = False
    depth: int = 0               # Nesting depth

@dataclass
class ComplexCalculateIntent:
    """Semantic intent for complex CALCULATE expressions"""
    base_measure: str
    filter_contexts: List[FilterContext] = field(default_factory=list)
    context_depth: int = 0
    has_conflicting_filters: bool = False
    conflict_description: Optional[str] = None
    is_deterministic: bool = True
    complexity_tier: Literal[4, 5] = 4  # Tier 4-5 (complex)

@dataclass
class RowContextIntent:
    """Semantic intent for EARLIER/EARLIEST functions"""
    base_expression: str
    offset: Optional[int] = None              # For EARLIER(col, offset)
    row_context_type: Literal["current", "earlier", "earliest"] = "current"
    ordering_column: str = ""                 # What defines row order
    partition_by: Optional[str] = None        # Window partition column
    is_deterministic: bool = True
    requires_sort_stability: bool = True

@dataclass
class IteratorIntent:
    """Semantic intent for SUMX/AVERAGEX/RANKX"""
    iterator_function: Literal["SUMX", "AVERAGEX", "AVERAGEX", "RANKX"] = "SUMX"
    table: str = ""
    expression: str = ""
    filter_contexts: List[FilterContext] = field(default_factory=list)
    nested_iterator: Optional["IteratorIntent"] = None
    output_type: str = "numeric"
    row_context_in_expression: bool = False
    nesting_depth: int = 1
```

### 6.2 Symbolic Execution Models (New)

```python
# src/semabridge/models/phase_2_symbolic_execution.py

from typing import Optional, Set, Any, List, Dict
from dataclasses import dataclass
from enum import Enum

class ValueType(str, Enum):
    """Type of value in symbolic execution"""
    INTEGER = "integer"
    FLOAT = "float"
    STRING = "string"
    BOOLEAN = "boolean"
    NULL = "null"
    UNKNOWN = "unknown"

@dataclass
class SymbolicValue:
    """Represents a value during symbolic execution"""
    python_type: type
    value_type: ValueType
    nullable: bool = True
    min_value: Optional[Any] = None
    max_value: Optional[Any] = None
    possible_values: Optional[Set[Any]] = None  # If finite set
    symbolic_expression: str = ""               # How value was computed
    constraints: List[str] = field(default_factory=list)  # Constraints on value

@dataclass
class SymbolicExecutionTrace:
    """Complete trace of symbolic execution"""
    step_number: int
    function: str                          # CALCULATE, FILTER, etc
    input_values: Dict[str, SymbolicValue]
    operation: str                         # Mathematical operation
    output_value: SymbolicValue
    context_changes: List[str]             # What changed in context

@dataclass
class EquivalenceProof:
    """Proof that DAX ≡ SQL"""
    dax_pattern: str
    sql_pattern: str
    is_equivalent: bool
    proof_method: Literal["symbolic_execution", "test_suite", "proven"] = "symbolic_execution"
    test_cases_passed: int = 0
    test_cases_failed: int = 0
    failed_tests: List[Dict[str, Any]] = field(default_factory=list)
    counterexample: Optional[Dict[str, Any]] = None  # If not equivalent
    symbolic_trace: List[SymbolicExecutionTrace] = field(default_factory=list)
    confidence_level: Literal["low", "medium", "high", "proven"] = "high"
```

### 6.3 Filter Context Tracking Models (New)

```python
# src/semabridge/models/phase_2_filter_context.py

@dataclass
class FilterStep:
    """Single step in filter application"""
    step_number: int
    line_number: int
    function: str                  # CALCULATE, FILTER, REMOVEFILTERS, etc
    filters_applied: List[str]
    filters_removed: List[str]
    context_depth_before: int
    context_depth_after: int
    semantic_operation: str         # What semantically happens

@dataclass
class FilterContextTrace:
    """Complete trace of filter application"""
    measure: str
    steps: List[FilterStep] = field(default_factory=list)
    final_filters: List[str] = field(default_factory=list)
    conflicts_detected: List[str] = field(default_factory=list)
    has_circular_dependency: bool = False
    is_deterministic: bool = True
    max_context_depth: int = 0

@dataclass
class FilterConflict:
    """Detected conflict in filter application"""
    conflict_type: Literal["circular", "contradictory", "ambiguous"] = "ambiguous"
    involved_filters: List[str]
    description: str = ""
    severity: Literal["warning", "error"] = "warning"
    suggested_resolution: Optional[str] = None
```

### 6.4 Extended TranslationResult (Phase 1 Extension)

```python
# Update existing TranslationResult to include Phase 2 fields

@dataclass
class TranslationResult:  # EXTENDS Phase 1 version
    # Phase 1 fields (unchanged)
    dax_expression: str
    generated_sql: str
    is_deterministic: bool
    complexity_tier: int
    # ...
    
    # Phase 2 NEW FIELDS
    correctness_proof: Optional[EquivalenceProof] = None
    filter_context_trace: Optional[FilterContextTrace] = None
    complex_calculate_intent: Optional[ComplexCalculateIntent] = None
    row_context_intent: Optional[RowContextIntent] = None
    iterator_intent: Optional[IteratorIntent] = None
    symbolic_execution_trace: Optional[List[SymbolicExecutionTrace]] = None
    required_window_functions: List[str] = field(default_factory=list)
    required_cte_depth: int = 0
    llm_fallback_used: bool = False
    llm_confidence: Optional[float] = None
```

---

## 7. VALIDATION & TESTING STRATEGY

### 7.1 Unit Testing (350+ new tests)

**FilterContextTracker Tests (100 tests)**
- Nested CALCULATE (2, 3, 4, 5 levels)
- REMOVEFILTERS combinations
- ADDFILTERS with conflict detection
- ALL() variations
- Edge cases: empty filters, circular references
- Performance: 1000 measures, <100ms

**SymbolicExecutor Tests (150 tests)**
- Arithmetic operations (addition, division, etc)
- NULL propagation (NULL + 5 = NULL)
- Type coercion (string + number)
- Value range tracking
- Constraint satisfaction
- Edge cases: overflow, underflow, precision loss

**Complex CALCULATE Tests (200 tests)**
- Nested CALCULATE (from real customer models)
- Filter context stacking
- REMOVEFILTERS/ADDFILTERS combinations
- Relationship overrides (USERELATIONSHIP)
- Time intelligence patterns (YTD, QTD, seasonal)
- Edge cases: empty tables, circular logic

**RowContextTranslator Tests (150 tests)**
- EARLIER patterns (simple and nested)
- EARLIEST patterns
- Row ordering
- Window frame specifications
- NULL handling in windows
- Edge cases: empty windows, single row

**IteratorTranslator Tests (150 tests)**
- SUMX patterns
- AVERAGEX patterns
- RANKX patterns
- Nested iterators (SUMX in AVERAGEX)
- Iterator with row context
- Filter contexts in iterators
- Edge cases: empty iteration sets, cardinality changes

**Integration Tests (150 tests)**
- Complex patterns end-to-end
- Multiple pattern combinations
- Error handling and fallback
- Performance benchmarks
- Regression from Phase 1

**Total Unit Tests:** 900+ new tests  
**Target Coverage:** 98%+ of Phase 2 code  
**Execution Time:** <10 minutes full suite

### 7.2 Integration Testing (230+ tests)

**Parity Tests with Golden Dataset (300+ tests)**
- Execute 500+ complex measures on Fabric
- Execute same measures on Snowflake
- Compare results row-by-row, aggregate
- Measure precision tolerance (<0.01% deviation)
- Performance comparison (<5 min per measure)

**End-to-End Workflow Tests (50+ tests)**
- Extract Fabric model → Analyze → Translate → Emit → Validate
- Verify data correctness throughout pipeline
- Test error handling and recovery
- Performance under load (1000 measures)

**Regression Tests from Phase 1 (100+ tests)**
- Ensure all Phase 1 patterns still work
- No performance degradation
- No correctness regressions
- Backward compatibility maintained

**Edge Case Coverage (100+ tests)**
- Extreme values (MIN/MAX for data types)
- NULL handling at each step
- Empty result sets
- Cardinality edge cases
- Time boundary conditions

**Total Integration Tests:** 580+ tests  
**Golden Dataset:** 500+ real customer measures  
**Execution Time:** <30 minutes full suite

### 7.3 Validation Checkpoints

**Checkpoint 1 (End of Week 2):** FilterContextTracker working
- All 100 filter context tests passing
- No Phase 1 regressions
- Ready for symbolic executor

**Checkpoint 2 (End of Week 3):** SymbolicExecutor proven
- All 150 symbolic execution tests passing
- Correctness proofs generated for simple patterns
- Edge case detection working

**Checkpoint 3 (End of Week 5):** Complex CALCULATE translation working
- All 200 CALCULATE tests passing
- 95%+ of complex CALCULATE patterns translated
- Parity validation on 100+ measures

**Checkpoint 4 (End of Week 7):** Full advanced translation working
- RowContextTranslator: 150 tests passing
- IteratorTranslator: 150 tests passing
- Combined patterns: working correctly
- Parity validation on 300+ measures

**Checkpoint 5 (End of Week 8):** Full parity suite passing
- 500+ measures with 98%+ semantic equivalence
- Zero Phase 1 regressions
- LLM fallback operational (<2% of measures)
- Performance <5 minutes per measure

**Checkpoint 6 (End of Week 12):** Ready for production
- All documentation complete
- Training materials ready
- Support team trained
- Deployment checklist complete

---

## 8. RISKS & CONSTRAINTS

### 8.1 Technical Risks

**Risk 1: Symbolic Execution Incompleteness** [HIGH]
- **Description:** Symbolic executor cannot prove equivalence for edge cases
- **Probability:** 40% (some patterns may be unprovable)
- **Impact:** HIGH (need LLM fallback for these)
- **Mitigation:** 
  - Start with patterns we know are deterministic
  - Build edge case test suite first
  - Use fallback gracefully
  - Document what's not provable

**Risk 2: CTE Explosion in SQL** [HIGH]
- **Description:** Complex CALCULATE → deeply nested CTEs → slow SQL
- **Probability:** 60% (real risk with 4-5 level CALCULATE)
- **Impact:** HIGH (measures timeout in Snowflake)
- **Mitigation:**
  - Profile SQL query plans early (Week 3)
  - Implement CTE materialization strategies
  - Alternative: use temp tables instead of CTEs
  - Performance gates: any measure >5 min gets optimization

**Risk 3: Row Context Ordering Ambiguity** [MEDIUM]
- **Description:** DAX row context ordering not always clear
- **Probability:** 30% (some patterns ambiguous)
- **Impact:** MEDIUM (incorrect results in edge cases)
- **Mitigation:**
  - Document ordering assumptions
  - Add explicit ordering detection
  - Symbol execute multiple orderings
  - Test with multiple sort orders

**Risk 4: Filter Context Conflicts** [MEDIUM]
- **Description:** Some filter combinations are semantically contradictory
- **Probability:** 25% (real in complex models)
- **Impact:** MEDIUM (incorrect results, hard to debug)
- **Mitigation:**
  - Implement conflict detection early (T2.1)
  - Generate detailed conflict reports
  - Provide manual resolution workflow
  - Document conflict patterns

**Risk 5: LLM Hallucination** [MEDIUM]
- **Description:** LLM generates plausible but incorrect SQL
- **Probability:** 20% (LLM quality improves, but risk remains)
- **Impact:** HIGH (silent data errors)
- **Mitigation:**
  - Mandatory validation gate (T2.7.2)
  - Human review for all LLM translations
  - Symbolic execution checks
  - Never deploy without validation

### 8.2 Architectural Risks

**Risk 6: Phase 1 Module Brittleness** [MEDIUM]
- **Description:** Phase 1 modules may not support semantic annotations well
- **Probability:** 30% (discovered during implementation)
- **Impact:** MEDIUM (need Phase 1 rework)
- **Mitigation:**
  - Compatibility testing early (Week 1)
  - Coordinate with Phase 1 team
  - Design Phase 2 modules as independent layer
  - Plan for Phase 1 enhancement if needed

**Risk 7: Snowflake Dialect Limitations** [MEDIUM]
- **Description:** Snowflake may not support required patterns (window functions, CTEs)
- **Probability:** 15% (Snowflake has most features, but edge cases possible)
- **Impact:** MEDIUM (need workarounds or LLM fallback)
- **Mitigation:**
  - Snowflake feature compatibility study (Week 1)
  - Document workarounds for unsupported patterns
  - Plan alternative syntax for edge cases

**Risk 8: Parity Testing Coverage Gaps** [MEDIUM]
- **Description:** Golden dataset doesn't cover all pattern combinations
- **Probability:** 40% (combinatorial explosion of patterns)
- **Impact:** MEDIUM (edge cases fail in production)
- **Mitigation:**
  - Auto-generate test cases (EdgeCaseTestGenerator)
  - Collect real customer models continuously
  - Expand golden dataset as new patterns found
  - Maintain regression tests

### 8.3 Operational Risks

**Risk 9: Team Skill Requirements** [MEDIUM]
- **Description:** Need very strong DAX + SQL engineers, scarce skill
- **Probability:** 70% (this is a constraint)
- **Impact:** MEDIUM (delays, quality issues)
- **Mitigation:**
  - Recruit early (before Phase 1 ends)
  - Knowledge transfer from Phase 1
  - Pair senior + junior engineers
  - Invest in training

**Risk 10: Performance Regression** [MEDIUM]
- **Description:** Phase 2 changes slow down Phase 1 functionality
- **Probability:** 50% (always risk with changes)
- **Impact:** HIGH (production impact)
- **Mitigation:**
  - Performance gates in CI/CD
  - Regression testing mandatory
  - Profile early and often
  - Rollback plan for any regression

---

## 9. SUCCESS CRITERIA

### 9.1 Functional Criteria

✅ **Complex DAX Translation Completeness**
- **Metric:** % of complex DAX patterns translated deterministically
- **Target:** 95%+ of CALCULATE, EARLIER, AVERAGEX patterns
- **Validation:** Test suite passing, golden dataset coverage
- **Definition of Success:** <2% LLM fallback required

✅ **Symbolic Execution Proof Generation**
- **Metric:** % of measures with formal correctness proof
- **Target:** 95%+ of translated measures have proof
- **Validation:** EquivalenceValidator reports
- **Definition of Success:** Automated proof for all deterministic patterns

✅ **Parity Score Achievement**
- **Metric:** Average semantic equivalence score across golden dataset
- **Target:** 98%+ equivalence (measured via parity testing)
- **Validation:** ParityTestRunner results
- **Definition of Success:** 98%+ of measures produce identical results (within tolerance)

### 9.2 Performance Criteria

✅ **Translation Speed**
- **Metric:** Average time to translate complex measure
- **Target:** <5 minutes per measure
- **Validation:** Performance benchmarks
- **Definition of Success:** 95% of measures translate in <5 minutes

✅ **Generated SQL Performance**
- **Metric:** Query execution time in Snowflake
- **Target:** <5 minutes for complex measure queries
- **Validation:** Query execution tests
- **Definition of Success:** Complex measures execute in <5 min on 10GB dataset

✅ **Test Suite Execution**
- **Metric:** Total time to run full test suite
- **Target:** <20 minutes for full suite
- **Validation:** CI/CD execution times
- **Definition of Success:** Fast feedback loop (<20 min)

### 9.3 Quality Criteria

✅ **Test Coverage**
- **Metric:** Code coverage of Phase 2 modules
- **Target:** 95%+ code coverage
- **Validation:** Code coverage reports
- **Definition of Success:** All modules >95% covered

✅ **Zero Regressions**
- **Metric:** Phase 1 test suite passing
- **Target:** 100% of Phase 1 tests still pass
- **Validation:** Regression test suite
- **Definition of Success:** No Phase 1 behavior change

✅ **Documentation Completeness**
- **Metric:** Pages of documentation
- **Target:** 1000+ pages
- **Validation:** Manual review + accessibility
- **Definition of Success:** Engineers can self-serve pattern translation

### 9.4 Customer Impact Criteria

✅ **Support Ticket Reduction**
- **Metric:** Reduction in "unsupported pattern" tickets
- **Target:** 85% reduction
- **Baseline:** Phase 1 ticket volume (~50/month)
- **Definition of Success:** <8 tickets/month for unsupported patterns

✅ **Model Coverage Improvement**
- **Metric:** % of customer models syncing successfully
- **Target:** Improvement from 90% to 98%
- **Validation:** Production deployment tracking
- **Definition of Success:** 98% of customer models sync without errors

---

## 10. TEAM STRUCTURE & OWNERSHIP

### 10.1 Recommended Team Composition

**Total: 4.5 FTE (12 weeks)**

```
Phase 2 Technical Lead (1 FTE)
├─ DAX Expert Engineer (1.5 FTE) [New hire or Phase 1 lead]
├─ SQL/Snowflake Engineer (1.5 FTE) [Phase 1 SQL engineer + 1x new]
├─ QA/Validation Engineer (1 FTE) [Lead validation + golden dataset]
└─ Infrastructure Engineer (0.5 FTE) [LLM optimization, CI/CD]
```

### 10.2 Ownership Boundaries

**Phase 2 Technical Lead** (1 FTE)
- **Owner:** Architecture + Risk management
- **Responsibilities:**
  - Design decisions (CTE vs temp tables, etc)
  - Risk mitigation
  - Team coordination
  - Stakeholder updates
  - Final integration checkpoint

**DAX Expert (1.5 FTE)**
- **Owner:** ComplexCalculateTranslator, RowContextTranslator, IteratorTranslator
- **Responsibilities:**
  - Semantic analysis (T2.3, T2.4, T2.5)
  - Pattern research and classification
  - Edge case identification
  - SymbolicExecutor design (with SQL engineer)

**SQL Engineer (1.5 FTE)**
- **Owner:** SymbolicExecutor, SQL generation optimization
- **Responsibilities:**
  - SQL generation (T2.3, T2.4, T2.5)
  - Query optimization
  - Performance profiling
  - FilterContextTracker SQL integration
  - Snowflake compatibility

**QA/Validation Engineer (1 FTE)**
- **Owner:** Testing strategy + Golden dataset
- **Responsibilities:**
  - Golden dataset expansion (T2.6.1)
  - Parity test infrastructure (T2.6.2-T2.6.5)
  - Unit test writing (all modules)
  - Regression testing
  - Edge case identification

**Infrastructure Engineer (0.5 FTE)**
- **Owner:** LLM optimization + CI/CD
- **Responsibilities:**
  - LLM caching and validation (T2.7)
  - CI/CD integration
  - Performance monitoring
  - Cost tracking

### 10.3 Review Process

**Architectural Review (Weekly)**
- Technical lead + DAX expert + SQL engineer
- Review design decisions
- Risk assessment
- Integration points

**Code Review (Per PR)**
- Minimum 2 engineers from different specialization
- Performance profiling required
- Test coverage validation
- Documentation completeness

**Parity Review (Per Milestone)**
- Compare results: Fabric vs Snowflake
- Symbolic execution proof review
- Correctness validation
- Edge case assessment

---

## 11. IMPLEMENTATION TIMELINE

### 11.1 Week-by-Week Plan

**WEEK 1: Foundation Design & Setup**
- **T2.1.1:** FilterContextTracker core implementation [2/5 days complete]
- **Setup:** Development environment, CI/CD setup, golden dataset schema
- **Review:** Architectural review, filter tracking strategy
- **Milestone:** Filter context infrastructure working

**WEEK 2: Filter Tracking & Symbolic Executor Design**
- **T2.1.1-T2.1.4:** Complete FilterContextTracker + tests
- **T2.2.1:** SymbolicExecutor core design + initial implementation
- **Review:** Filter tracking completeness, symbolic execution strategy
- **Milestone:** Filter tracking complete, symbolic executor 40% done

**WEEK 3: Symbolic Execution & Phase 1 Integration**
- **T2.2.1-T2.2.4:** Complete SymbolicExecutor, EquivalenceValidator
- **T2.3.1:** Start ComplexCalculateTranslator design
- **Review:** Symbolic execution proof generation, integration points
- **Milestone:** Symbolic executor complete, CALCULATE design ready

**WEEK 4: Complex CALCULATE Translation (Parallel Tracks)**
- **DAX Track:** T2.3.1-T2.3.2 (CALCULATE + USERELATIONSHIP implementation)
- **SQL Track:** T2.3.1 (SQL generation for CALCULATE)
- **QA Track:** T2.4.1-T2.4.2 (RowContext translator start)
- **Milestone:** CALCULATE patterns translating, RowContext framework ready

**WEEK 5: Advanced Translation Patterns**
- **T2.3.3-T2.3.5:** Complete CALCULATE translator + integration
- **T2.4.1-T2.4.3:** Complete RowContextTranslator
- **T2.5.1:** Start IteratorTranslator
- **Review:** Pattern translation completeness, parity preparation
- **Milestone:** CALCULATE + RowContext complete, Iterator foundation

**WEEK 6: Iterator & Parallel Validation Start**
- **T2.5.1-T2.5.3:** Complete IteratorTranslator
- **T2.6.1:** Begin golden dataset expansion
- **Review:** Iterator patterns, parity testing strategy
- **Milestone:** All translators complete, parity testing starting

**WEEK 7: Full Parity Testing**
- **T2.6.2-T2.6.5:** Execute full parity test suite
- **T2.7.1-T2.7.3:** LLM optimization implementation
- **Debugging:** Fix any translation failures, edge cases
- **Review:** Parity results, LLM fallback quality
- **Milestone:** 500+ measures parity tested, LLM optimization operational

**WEEK 8: Parity Completion & LLM Tuning**
- **T2.6.5:** Complete parity testing, fix failures
- **T2.7.4:** LLM cost optimization finalization
- **Performance:** Query optimization for slow measures
- **Review:** 98% parity achieved, performance acceptable
- **Milestone:** Phase 2 code complete, ready for documentation

**WEEK 9-10: Documentation Sprint 1**
- **T2.8.1:** Complex Pattern Catalog (50% complete)
- **T2.8.2:** Translation Strategy Guide (50% complete)
- **Code cleanup:** Remove debug logging, optimize
- **Performance:** Final profiling and optimization
- **Milestone:** 50% documentation complete

**WEEK 11-12: Documentation Sprint 2 & Launch Prep**
- **T2.8.1-T2.8.4:** Complete all documentation
- **Training:** Team training for Phase 2 patterns
- **Handoff:** Documentation ready for customer support
- **Final review:** Code quality, test coverage, documentation
- **Deployment:** Phase 2 ready for production
- **Milestone:** Phase 2 complete, production ready

### 11.2 Milestone Checkpoints

| Milestone | Week | Criteria | Owner |
|-----------|------|----------|-------|
| Filter Context Infrastructure | 2 | T2.1 complete, 100 tests passing | DAX Lead |
| Symbolic Execution Proof | 3 | T2.2 complete, equivalence proofs working | DAX Lead + SQL |
| CALCULATE Translation | 5 | T2.3 complete, 95% coverage | SQL Lead |
| RowContext Translation | 5 | T2.4 complete | SQL Lead |
| Iterator Translation | 6 | T2.5 complete | SQL Lead |
| Full Parity Validation | 8 | 500+ measures, 98% equivalence | QA Lead |
| LLM Optimization Ready | 8 | <2% fallback, 90% cost reduction | Infra Lead |
| Documentation Complete | 12 | 1000+ pages, ready for support | Tech Writer |
| **Production Ready** | **12** | **All criteria met** | **Tech Lead** |

### 11.3 Critical Path

**Critical Path:** T2.1 → T2.2 → T2.3 → T2.6 (foundation → proof → translation → validation)

**Non-Critical Path:** T2.7 (LLM optimization can slip 1-2 weeks if needed)

**Parallel Opportunities:**
- T2.4 + T2.5 can run parallel to T2.3 (after T2.2)
- T2.6 can start after T2.3 (doesn't need T2.4, T2.5)
- T2.8 can start after T2.5

---

## 12. GO / NO-GO CRITERIA

### 12.1 Definitions of Success

**Code Complete:**
- ✅ All 4,450 lines of code written and checked in
- ✅ All unit tests passing (900+)
- ✅ All integration tests passing (580+)
- ✅ Code coverage >95%
- ✅ No Phase 1 regressions
- ✅ Documentation complete (1000+ pages)

**Technical Validation:**
- ✅ 95%+ of complex DAX patterns translate deterministically
- ✅ <2% require LLM fallback
- ✅ 98%+ parity score on golden dataset
- ✅ Zero silent data errors
- ✅ <5 minute translation + execution per measure
- ✅ Symbolic execution proofs generated for all deterministic patterns

**Production Readiness:**
- ✅ Monitoring and alerting configured
- ✅ Runbook documentation complete
- ✅ Support team trained
- ✅ Rollback plan tested
- ✅ Performance baselines established
- ✅ Cost tracking operational

### 12.2 Blockers Preventing Completion

**Would Block Phase 2 Completion:**
1. **Symbolic Executor Not Provable** — Cannot generate correctness proofs for 30%+ of patterns
   - Mitigation: Fallback to extended LLM + human review
   - Threshold: >95% provable = proceed, <95% = reassess approach

2. **CTE Performance Unsolvable** — Generated SQL routinely times out (>10 min)
   - Mitigation: Switch to temp tables, parallel queries
   - Threshold: >80% of measures <5 min = proceed

3. **Filter Conflict Unresolvable** — Complex CALCULATE patterns have logical contradictions
   - Mitigation: Implement conflict resolution algorithm
   - Threshold: Can resolve 90% of conflicts = proceed

4. **Phase 1 Module Incompatible** — Phase 1 modules cannot be extended without major rework
   - Mitigation: Implement Phase 2 as independent layer, wrap Phase 1
   - Threshold: Can integrate with <10% Phase 1 changes = proceed

### 12.3 Extension Criteria

**Criteria for Extending Phase 2 Schedule:**

- **If <95% of patterns translate:** Add 1-2 weeks to optimize translators
- **If parity <95%:** Add 1-2 weeks to fix edge cases
- **If documentation incomplete:** Extend week 12-14 for docs completion
- **If performance issues:** Extend 1-2 weeks for query optimization
- **If team skill gaps:** Reduce scope (defer T2.7 LLM optimization to Phase 3)

**Criteria for Reducing Scope:**
- If LLM optimization (T2.7) blocked: Defer to Phase 3 (increases LLM fallback to 5%)
- If row context (T2.4) problematic: Implement basic version, defer complex patterns to Phase 3
- If iterator (T2.5) too complex: Implement SUMX only, defer AVERAGEX/RANKX to Phase 3

---

## 13. RECOMMENDATIONS

### 13.1 Implementation Priorities

**Priority 1 (Must Have):**
1. Complex CALCULATE translation (T2.3)
   - 60% of Phase 2 benefit
   - Real customer need
   - Foundation for other translators

2. Symbolic execution proof (T2.2)
   - Ensures correctness
   - Eliminates LLM for 95% of cases
   - Critical for production confidence

3. Advanced parity validation (T2.6)
   - Proves Phase 2 works
   - Finds edge cases
   - Confidence builder

**Priority 2 (Should Have):**
4. Row context translation (T2.4)
   - 20% of Phase 2 benefit
   - Important for real models
   - Medium complexity

5. Iterator translation (T2.5)
   - 10% of Phase 2 benefit
   - Less common patterns
   - High complexity

**Priority 3 (Nice to Have):**
6. LLM optimization (T2.7)
   - Cost reduction
   - Can defer if needed
   - Orthogonal to core translation

### 13.2 What to Prototype First

**Prototype 1: CALCULATE Translation (Week 1-3)**
- Focus on 90% of real patterns
- Intentionally skip edge cases in prototype
- Goal: Prove approach feasible
- Success: 10 complex CALCULATE patterns translate correctly

**Prototype 2: Symbolic Execution (Week 2-3)**
- Simple proof generation for basic operations
- Build out for CALCULATE proofs
- Goal: Prove equivalence for CALCULATE
- Success: 80% of CALCULATE proofs generate automatically

**Prototype 3: Parity Testing (Week 4-5)**
- Run on 50-100 real customer measures
- Compare Fabric vs Snowflake results
- Goal: Find edge cases, validate approach
- Success: 95%+ parity on sample dataset

### 13.3 What to Defer to Phase 3

**Defer to Phase 3:**
- ❌ RLS (Row-Level Security) integration — Phase 3 enterprise feature
- ❌ Bidirectional sync — Phase 3 advanced feature
- ❌ Advanced M Query transformation — Phase 1 extension, not Phase 2
- ❌ Model-level governance — Phase 3 enterprise
- ❌ Cortex AI native integration — Phase 3 semantic runtime

### 13.4 Optimization Opportunities

**Early Wins (Can implement in Phase 2):**
1. **CTE Materialization** — Cache complex CTEs as temp tables
   - Impact: 2-3x performance improvement
   - Effort: 2 days (SQL engineer)
   - ROI: High

2. **Filter Context Precompilation** — Pre-compute filter combinations
   - Impact: Faster translation
   - Effort: 3 days
   - ROI: Medium (developer experience, not user-facing)

3. **Symbolic Execution Caching** — Cache proofs by pattern hash
   - Impact: 80% faster re-translation
   - Effort: 2 days
   - ROI: High (repeated patterns common)

**Future Optimizations (Phase 3+):**
- GPU-accelerated symbolic execution
- Distributed parity testing (across multiple Snowflake warehouses)
- LLM model fine-tuning for Fabric-specific patterns
- Query parallelization for large measures

### 13.5 Expected Outcomes

**By End of Phase 2:**

**Capabilities:**
- ✅ 95%+ of real DAX patterns translate deterministically
- ✅ <2% require LLM fallback (vs 10% in Phase 1)
- ✅ Formal proof of correctness for all translated measures
- ✅ 98%+ semantic equivalence achieved
- ✅ Support ticket volume for "unsupported patterns" down 85%

**Product Impact:**
- ✅ 98% of customer models sync successfully (vs 90% Phase 1)
- ✅ No more "unsupported pattern" exceptions
- ✅ Customers deploy complex Fabric models to Snowflake confidently
- ✅ Sync time <5 min for 95% of measures

**Engineering Investment:**
- ✅ 4,450 lines of well-tested code
- ✅ 1,480+ test cases (unit + integration)
- ✅ 1000+ pages of documentation
- ✅ Reusable components for Phase 3+

**Team Growth:**
- ✅ DAX engineers now expert in symbolic execution
- ✅ SQL engineers experienced with complex translation
- ✅ QA team expert in parity validation
- ✅ Reusable patterns for future phases

---

## APPENDIX: NEXT PHASES PREVIEW

### Phase 3: Semantic Runtime & Enterprise Semantics (12 weeks)

**What Phase 3 Adds:**
- RLS (Row-Level Security) for multi-tenant models
- Bidirectional sync (Snowflake → Fabric)
- Cortex AI native search integration
- Dynamic measure definitions
- Enterprise audit trail + lineage

### Phase 4: Optimization, Governance & Scale (12 weeks)

**What Phase 4 Adds:**
- Performance optimization (10x faster sync)
- Distributed sync for 10,000+ measures
- Enterprise governance framework
- Cost attribution per measure
- Multi-tenant platform

---

**End of Phase 2 Implementation Plan**

