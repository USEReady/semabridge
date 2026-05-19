# Phase 1 Implementation Plan
## Fabric Semantic Logic Preservation & Snowflake Semantic Execution

**Phase Duration:** 8 weeks  
**Target Team Size:** 3-4 engineers  
**Start Date:** Immediate  
**Status:** Ready for Execution

---

## 1. Phase 1 Scope Boundary

### 1.1 EXPLICITLY INCLUDED IN PHASE 1

**Core Translation Infrastructure:**
- ✅ Enhanced DAX parser with semantic-aware AST annotations
- ✅ Semantic Context Analyzer (understand what measures mean)
- ✅ Tier 1 → Tier 4 translation patterns (simple → complex CALCULATE)
- ✅ Basic M Query transformer (SELECT, FILTER, simple transforms)

**Validation & Parity:**
- ✅ Parity comparison engine (Fabric vs Snowflake execution)
- ✅ Test case generator (automatic golden test set)
- ✅ Aggregate parity validation (SUM, COUNT, AVG)
- ✅ Regression detection framework (baseline → compare pattern)

**Dependency Management:**
- ✅ Semantic Dependency Graph (measure → measure, measure → column)
- ✅ Topological sort for materialization ordering
- ✅ Cycle detection for circular dependencies

**Supporting Infrastructure:**
- ✅ Semantic Intent data model (Python dataclasses)
- ✅ Extended translation metadata (DAX → SQL tracing)
- ✅ Phase 1 validation test suite (250+ test cases)

### 1.2 EXPLICITLY OUT OF SCOPE FOR PHASE 1

**Complex DAX Patterns (Defer to Phase 2):**
- ❌ EARLIER/EARLIEST row context reference
- ❌ RANKX implementation (complex)
- ❌ Complex nested CALCULATE expressions (>3 levels deep)
- ❌ USERELATIONSHIP dynamic relationship switching
- ❌ Custom DAX functions

**M Query Advanced Support (Defer to Phase 2):**
- ❌ Custom M functions
- ❌ Conditional logic (if/then/else in M)
- ❌ Complex transformations (nested Table operations)
- ❌ Parameter binding/resolution
- ❌ Query folding optimization analysis

**RLS & Security:**
- ❌ Row-Level Security integration
- ❌ Access control policies
- ❌ Metadata governance (Phase 2)

**Deployment & Operations:**
- ❌ Snowflake DDL generation (that exists; this is translation)
- ❌ Change tracking/audit logging (Phase 2)
- ❌ Performance monitoring/dashboards
- ❌ Multi-tenant support

**Optimization:**
- ❌ Translation caching (Phase 2)
- ❌ Parallel translation (Phase 2)
- ❌ Incremental sync (Phase 2)

**UI/UX:**
- ❌ Frontend enhancements
- ❌ Visualization of dependency graphs
- ❌ Interactive validation reports

### 1.3 Scope Creep Prevention

**What Happens If Someone Asks For:**

| Request | Response |
|---------|----------|
| "Can we support RANKX?" | No. Defer to Phase 2. Create backlog item. |
| "Can we auto-generate indexes?" | No. Out of scope. Reference Phase 4. |
| "Can we parallelize translation?" | No. Phase 2 optimization. Phase 1 is sequential. |
| "Can we cache translations?" | No. Phase 2. Phase 1 translates on-demand. |
| "Can we handle EARLIER?" | No. Block with error message, document workaround. |

---

## 2. Core Deliverables

### D1: Enhanced DAX Parser with Semantic Annotations

**Purpose:**  
Upgrade existing recursive-descent parser to generate AST nodes annotated with semantic metadata (aggregation targets, context requirements, dependency references).

**Inputs:**
- DAX expression (string)
- OSI model context (for column/measure resolution)

**Outputs:**
- Annotated AST (tree of DaxAstNode + semantic metadata)
- SemanticIntent classification (understanding of what expression means)
- Complexity tier assignment (1-5)

**Current State:** Parser exists; lacks semantic annotations
**Effort:** 1.5 weeks
**Owner:** DAX Engineer

---

### D2: Semantic Context Analyzer

**Purpose:**  
Determine what a DAX expression semantically means (what are we aggregating? over what context? what relationships matter?).

**Inputs:**
- Annotated AST (from D1)
- OSI model + SML metadata
- Query filter context (if provided)

**Outputs:**
- SemanticIntent object (aggregation_type, targets, context_type, requirements)
- Context dependency list (which relationships needed?)
- Risk assessment (potential parity issues)

**Current State:** Doesn't exist
**Effort:** 2 weeks
**Owner:** Semantic Engineer
**Dependencies:** D1 (needs annotated AST)

---

### D3: Tier 2-4 DAX Translation Expansion

**Purpose:**  
Implement translation patterns for Tier 2 (arithmetic, CALCULATE), Tier 3 (time intelligence), and simple Tier 4 (CALCULATE with FILTER/ALL).

**Inputs:**
- Annotated AST
- SemanticIntent
- Translation rule library

**Outputs:**
- Snowflake SQL expression
- Translation metadata (which patterns matched, confidence level)

**Current State:** Tier 1 works; Tier 2-4 partial
**Effort:** 2.5 weeks
**Owner:** DAX Engineer + SQL Engineer
**Dependencies:** D1, D2 (needs semantic understanding)

---

### D4: Parity Validation Framework

**Purpose:**  
Build infrastructure to compare DAX execution (Fabric) vs SQL execution (Snowflake) for semantic equivalence.

**Inputs:**
- Measure definition (DAX)
- Translated SQL
- Test contexts (filter conditions)
- Sample data

**Outputs:**
- Parity report (pass/fail per test case)
- Deviation %, result comparison
- Recommendation (pass/warn/fail)

**Current State:** Doesn't exist
**Effort:** 2 weeks
**Owner:** QA/Validation Engineer
**Dependencies:** D3 (needs SQL to validate against)

---

### D5: Semantic Dependency Graph

**Purpose:**  
Build complete map of measure interdependencies; enable topological ordering and cycle detection.

**Inputs:**
- OSI model (all measures)
- Measure definitions (DAX expressions)
- Relationship definitions

**Outputs:**
- SemanticGraph object (nodes, edges, metadata)
- Topologically sorted measure list
- Cycle detection report

**Current State:** Basic measure dependency resolver exists
**Effort:** 1.5 weeks
**Owner:** Infrastructure Engineer
**Dependencies:** D2 (needs semantic analysis)

---

### D6: Basic M Query Transformer

**Purpose:**  
Parse simple M queries; translate SELECT/FILTER/TRANSFORM patterns to SQL.

**Inputs:**
- M code (string)
- Data source mapping (M table names → Snowflake tables)

**Outputs:**
- SQL equivalent (or "not supported" with fallback)
- Transformation metadata

**Current State:** M code extracted but not transformed
**Effort:** 1 week
**Owner:** SQL Engineer
**Dependencies:** None (parallel)

---

### D7: Phase 1 Validation Test Suite

**Purpose:**  
Build comprehensive test dataset for validating semantic translation.

**Inputs:**
- Real Fabric semantic models (5-10 diverse models)
- DAX expression library (250+ patterns)
- Test case generation rules

**Outputs:**
- Golden dataset (test measures + expected results)
- Parity test cases (3000+ test scenarios)
- Regression test baseline

**Current State:** Partial test suite exists
**Effort:** 1.5 weeks
**Owner:** QA/Validation Engineer
**Dependencies:** D4 (needs parity framework to generate tests)

---

## 3. Module-Level Breakdown

### Module 1: SemanticAstParser (Enhanced from existing)

**File Location:**  
`src/semabridge/converter/semantic_ast_parser.py`

**Purpose:**  
Upgrade existing `dax_ast_parser.py` to generate semantic annotations.

**Exports:**
```python
class SemanticAstParser:
    def parse(self, dax_expr: str, context: ParsingContext) -> AnnotatedAstNode
    def analyze_aggregation_targets(node: AstNode) -> List[str]
    def extract_filter_predicates(node: AstNode) -> List[FilterPredicate]
```

**Data Contracts:**
- Input: DAX string + OSI model
- Output: AnnotatedAstNode with semantic metadata
- Errors: DaxSyntaxError (with line/column), SemanticAnalysisError

**Dependencies:**
- Existing `dax_ast_parser.py` (will refactor)
- OSI model definitions
- Standard library (AST representation)

**Success Criteria:**
- Parses all Tier 1-3 patterns without error
- Correctly identifies aggregation targets
- Correctly classifies complexity tiers

---

### Module 2: SemanticContextAnalyzer (New)

**File Location:**  
`src/semabridge/converter/semantic_context_analyzer.py`

**Purpose:**  
Determine semantic intent from annotated AST.

**Exports:**
```python
class SemanticContextAnalyzer:
    def analyze(
        self,
        node: AnnotatedAstNode,
        model: OSIModel,
        query_context: Optional[QueryContext] = None
    ) -> SemanticIntent
    
    def infer_aggregation_target(node: AstNode) -> str
    def detect_time_intelligence(node: AstNode) -> Optional[TimeIntelligenceType]
    def identify_relationship_requirements(node: AstNode) -> List[str]
```

**Data Contracts:**
- Input: AnnotatedAstNode, OSIModel, QueryContext
- Output: SemanticIntent (dataclass)
- Errors: SemanticAnalysisError (with context about what failed)

**Dependencies:**
- SemanticAstParser (previous module)
- OSI model types
- Relationship definitions

**Success Criteria:**
- Correctly identifies aggregation types for all Tier 1-2 patterns
- Detects time intelligence patterns with 100% recall
- Flags ambiguous expressions requiring manual review

---

### Module 3: DaxTranslationEngine (Enhanced from existing)

**File Location:**  
`src/semabridge/converter/dax_translation_engine.py` (rename/refactor current)

**Purpose:**  
Generate Snowflake SQL from semantic intent + annotated AST.

**Exports:**
```python
class DaxTranslationEngine:
    def translate(
        self,
        ast: AnnotatedAstNode,
        semantic_intent: SemanticIntent,
        model: OSIModel
    ) -> TranslationResult
    
    def translate_tier1(expr: str, context: TranslationContext) -> str
    def translate_tier2(ast: AstNode, context: TranslationContext) -> str
    def translate_tier3_time_intelligence(
        time_intel_type: str,
        aggregation: str,
        date_column: str
    ) -> str
    def translate_tier4_calculate(
        ast: AstNode,
        filters: List[FilterClause],
        context: TranslationContext
    ) -> str
```

**Data Contracts:**
- Input: AnnotatedAstNode, SemanticIntent, OSIModel
- Output: TranslationResult (sql_expression, metadata, confidence_level)
- Errors: TranslationNotSupportedError (with suggestion for fallback)

**Dependencies:**
- SemanticContextAnalyzer
- SemanticAstParser
- SQL generation utilities

**Success Criteria:**
- Generates valid Snowflake SQL for all supported patterns
- Tier 1-2 patterns: 100% success rate
- Tier 3 patterns: 95%+ success rate
- Tier 4 patterns: 80%+ success rate (remainder → LLM fallback)

---

### Module 4: ParityValidationEngine (New)

**File Location:**  
`src/semabridge/validators/parity_validation_engine.py`

**Purpose:**  
Compare DAX execution vs SQL execution for semantic equivalence.

**Exports:**
```python
class ParityValidationEngine:
    def validate_measure(
        self,
        measure_name: str,
        dax_expr: str,
        sql_expr: str,
        test_contexts: List[FilterContext]
    ) -> MeasureParityReport
    
    def execute_in_fabric(
        dax: str,
        context: FilterContext
    ) -> Any
    
    def execute_in_snowflake(
        sql: str,
        context: FilterContext
    ) -> Any
    
    def compare_results(
        fabric_result: Any,
        snowflake_result: Any,
        tolerance: float = 0.001
    ) -> ComparisonResult
```

**Data Contracts:**
- Input: Measure definition, SQL translation, test contexts
- Output: ParityReport (pass/fail, deviation %, diagnostics)
- Errors: ExecutionError (with query that failed)

**Dependencies:**
- Fabric API client (for DAX execution)
- Snowflake connector
- Numeric comparison utilities

**Success Criteria:**
- Correctly identifies parity matches (>99% precision)
- Detects semantic mismatches (>95% recall)
- Produces useful error messages for failures

---

### Module 5: SemanticDependencyGraph (New)

**File Location:**  
`src/semabridge/core/semantic_dependency_graph.py`

**Purpose:**  
Build and analyze complete semantic model dependency graph.

**Exports:**
```python
class SemanticDependencyGraph:
    def __init__(self, model: OSIModel)
    
    def build(self) -> None
    def add_node(node_id: str, node: SemanticNode) -> None
    def add_edge(from_id: str, to_id: str, edge: SemanticEdge) -> None
    
    def detect_cycles(self) -> List[List[str]]
    def topological_sort(self) -> List[str]
    def get_dependents(node_id: str) -> Set[str]
    def get_dependencies(node_id: str) -> Set[str]
    
    def validate(self) -> ValidationReport
```

**Data Contracts:**
- Input: OSIModel (measures, columns, relationships)
- Output: SemanticGraph object with nodes/edges/metadata
- Errors: CyclicDependencyError (with cycle path)

**Dependencies:**
- OSI model types
- Graph algorithms (topological sort)

**Success Criteria:**
- Detects all cycles (100% recall)
- Produces correct topological ordering
- Handles circular references correctly

---

### Module 6: MQueryTransformer (New)

**File Location:**  
`src/semabridge/converter/m_query_transformer.py`

**Purpose:**  
Translate simple M Query patterns to SQL.

**Exports:**
```python
class MQueryTransformer:
    def transform(
        self,
        m_code: str,
        source_mappings: Dict[str, str]  # M table → Snowflake table
    ) -> MQueryTransformResult
    
    def is_translatable(m_code: str) -> bool
    def parse_m_expression(code: str) -> MAstNode
    def translate_m_ast(ast: MAstNode) -> str
```

**Data Contracts:**
- Input: M code (string), source mappings
- Output: MQueryTransformResult (sql_expression, is_supported, metadata)
- Errors: MQueryNotSupportedError (with reason)

**Dependencies:**
- None (can develop independently)

**Success Criteria:**
- Translates simple SELECT patterns (100% success)
- Translates FILTER patterns (95% success)
- Correctly marks unsupported patterns

---

## 4. Internal Data Structures

### 4.1 SemanticIntent (Dataclass)

```python
from dataclasses import dataclass, field
from typing import List, Optional
from enum import Enum

class AggregationType(str, Enum):
    SUM = "sum"
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    AVERAGE = "average"
    MIN = "min"
    MAX = "max"
    NONE = "none"

class FilterContextType(str, Enum):
    INHERITED = "inherited"
    EXPLICIT = "explicit"
    MODIFIED = "modified"

class TimeIntelligenceType(str, Enum):
    TOTALYTD = "totalytd"
    TOTALMTD = "totalmtd"
    TOTALQTD = "totalqtd"
    SAMEPERIODLASTYEAR = "sameperiodlastyear"
    PREVIOUSYEAR = "previousyear"
    PREVIOUSMONTH = "previousmonth"
    DATEADD = "dateadd"
    NONE = "none"

class TranslationStrategy(str, Enum):
    DETERMINISTIC = "deterministic"
    AST_BASED = "ast_based"
    LLM_FALLBACK = "llm_fallback"
    UNSUPPORTED = "unsupported"

@dataclass
class SemanticIntent:
    """Represents the semantic meaning of a DAX expression."""
    
    # Core aggregation semantics
    aggregation_type: AggregationType
    aggregation_targets: List[str]  # Column names being aggregated
    aggregation_table: Optional[str] = None  # Table containing target columns
    
    # Context semantics
    filter_context_type: FilterContextType
    filter_context_overrides: List[str] = field(default_factory=list)  # ALL/ALLEXCEPT columns
    row_context_required: bool = False
    
    # Time intelligence
    time_intelligence_type: TimeIntelligenceType = TimeIntelligenceType.NONE
    time_intelligence_column: Optional[str] = None
    time_intelligence_calendar_type: str = "gregorian"  # gregorian or fiscal
    
    # Relationship context
    relationship_references: List[str] = field(default_factory=list)  # Relationship IDs
    cross_filter_required: bool = False
    
    # Translation metadata
    complexity_tier: int  # 1-5
    translation_strategy: TranslationStrategy
    confidence_level: float = 1.0  # 0.0-1.0
    
    # Quality metrics
    semantic_risks: List[str] = field(default_factory=list)
    parity_assumptions: List[str] = field(default_factory=list)
    
    def is_deterministic_translatable(self) -> bool:
        return self.translation_strategy in (
            TranslationStrategy.DETERMINISTIC,
            TranslationStrategy.AST_BASED
        )
```

### 4.2 AnnotatedAstNode (Enhanced AST Node)

```python
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

@dataclass
class AnnotatedAstNode:
    """AST node with semantic annotations."""
    
    node_type: str  # "function_call", "column_ref", "measure_ref", "binary_op", etc.
    value: str  # Node content ("SUM", "Amount", etc.)
    
    # Existing AST properties
    children: List['AnnotatedAstNode'] = field(default_factory=list)
    line: int = 0
    column: int = 0
    
    # NEW: Semantic annotations
    resolved_target: Optional[str] = None  # Column/measure this node references
    aggregation_type: Optional[AggregationType] = None
    requires_context: Optional[FilterContextType] = None
    dependencies: List[str] = field(default_factory=list)  # Measure refs
    
    # Type information
    inferred_type: str = "unknown"  # "numeric", "string", "date", etc.
    is_nullable: bool = True
    
    # Analysis metadata
    semantic_warnings: List[str] = field(default_factory=list)
    translation_hints: Dict[str, Any] = field(default_factory=dict)
    
    def get_all_dependencies(self) -> List[str]:
        """Recursively get all measure dependencies."""
        all_deps = set(self.dependencies)
        for child in self.children:
            all_deps.update(child.get_all_dependencies())
        return list(all_deps)
    
    def find_nodes_by_type(self, node_type: str) -> List['AnnotatedAstNode']:
        """Find all nodes of specific type in subtree."""
        matching = []
        if self.node_type == node_type:
            matching.append(self)
        for child in self.children:
            matching.extend(child.find_nodes_by_type(node_type))
        return matching
```

### 4.3 TranslationResult (Dataclass)

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class TranslationResult:
    """Result of translating a DAX expression to SQL."""
    
    # Translation output
    sql_expression: str
    is_successful: bool
    
    # Metadata
    semantic_intent: SemanticIntent
    complexity_tier: int
    translation_strategy: TranslationStrategy
    dax_original: str
    
    # Quality metrics
    confidence_level: float  # 0.0-1.0
    applied_patterns: List[str] = field(default_factory=list)
    
    # Diagnostics
    warnings: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    fallback_suggested: bool = False
    fallback_reason: Optional[str] = None
    
    # Lineage/tracing
    translation_trace: str = ""  # Human-readable translation path
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize for metadata storage."""
        return {
            "sql_expression": self.sql_expression,
            "is_successful": self.is_successful,
            "complexity_tier": self.complexity_tier,
            "confidence_level": self.confidence_level,
            "warnings": self.warnings,
            "assumptions": self.assumptions,
            "translation_trace": self.translation_trace,
        }
```

### 4.4 MeasureParityReport (Dataclass)

```python
from dataclasses import dataclass
from typing import List, Dict, Any

@dataclass
class TestCaseResult:
    """Result for a single test case."""
    filter_context: Dict[str, Any]
    fabric_result: Any
    snowflake_result: Any
    deviation_pct: float
    passed: bool  # deviation < tolerance
    error_message: Optional[str] = None

@dataclass
class MeasureParityReport:
    """Parity validation results for a single measure."""
    
    measure_name: str
    dax_expression: str
    sql_expression: str
    
    # Test results
    test_cases: List[TestCaseResult]
    passed_tests: int
    failed_tests: int
    
    # Overall metrics
    pass_rate: float  # 0-100%
    average_deviation_pct: float
    max_deviation_pct: float
    
    # Status
    overall_status: str  # "pass", "warn", "fail"
    parity_score: float  # 0-100% confidence in parity
    
    # Recommendations
    recommendations: List[str]
    requires_manual_review: bool
    
    def to_json(self) -> Dict[str, Any]:
        """Serialize for reporting."""
        return {
            "measure_name": self.measure_name,
            "pass_rate": self.pass_rate,
            "average_deviation_pct": self.average_deviation_pct,
            "parity_score": self.parity_score,
            "overall_status": self.overall_status,
            "failed_cases": [
                {
                    "context": tc.filter_context,
                    "deviation": tc.deviation_pct,
                    "error": tc.error_message
                }
                for tc in self.test_cases
                if not tc.passed
            ],
            "recommendations": self.recommendations,
        }
```

### 4.5 SemanticNode & SemanticEdge (Graph Components)

```python
from dataclasses import dataclass
from enum import Enum

class NodeType(str, Enum):
    MEASURE = "measure"
    COLUMN = "column"
    TABLE = "table"
    RELATIONSHIP = "relationship"
    HIERARCHY = "hierarchy"

class EdgeType(str, Enum):
    DEPENDS_ON = "depends_on"
    REFERENCES = "references"
    AGGREGATES = "aggregates"
    FILTERS = "filters"
    JOINS = "joins"

@dataclass
class SemanticNode:
    """Node in semantic dependency graph."""
    node_id: str
    node_type: NodeType
    name: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # For measures
    dax_expression: Optional[str] = None
    sql_expression: Optional[str] = None
    complexity_tier: int = 0
    
    # For columns
    source_table: Optional[str] = None
    data_type: Optional[str] = None
    
    # Risk assessment
    has_circular_dependency: bool = False
    translation_status: str = "pending"  # pending, success, warning, failed

@dataclass
class SemanticEdge:
    """Edge in semantic dependency graph."""
    from_node_id: str
    to_node_id: str
    edge_type: EdgeType
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Cardinality (for relationship edges)
    cardinality: Optional[str] = None  # "1:1", "1:N", etc.
    
    # Weight for prioritization
    criticality: float = 1.0  # 0.0-1.0
```

### 4.6 SemanticGraph (Graph Container)

```python
from dataclasses import dataclass
from typing import Set, List, Dict

@dataclass
class SemanticGraph:
    """Complete semantic dependency graph."""
    
    nodes: Dict[str, SemanticNode]
    edges: List[SemanticEdge]
    
    # Computed properties
    cycles: List[List[str]] = field(default_factory=list)
    materialization_order: List[str] = field(default_factory=list)
    conflict_zones: List[str] = field(default_factory=list)
    
    # Metadata
    created_at: str = field(default_factory=lambda: str(datetime.now()))
    validation_status: str = "pending"  # pending, valid, invalid
    validation_errors: List[str] = field(default_factory=list)
    
    def get_node(self, node_id: str) -> Optional[SemanticNode]:
        return self.nodes.get(node_id)
    
    def get_outgoing_edges(self, node_id: str) -> List[SemanticEdge]:
        """Get edges leaving this node."""
        return [e for e in self.edges if e.from_node_id == node_id]
    
    def get_incoming_edges(self, node_id: str) -> List[SemanticEdge]:
        """Get edges entering this node."""
        return [e for e in self.edges if e.to_node_id == node_id]
    
    def get_all_dependents(self, node_id: str) -> Set[str]:
        """Recursively get all nodes depending on this node."""
        dependents = set()
        for edge in self.get_outgoing_edges(node_id):
            dependent = edge.to_node_id
            dependents.add(dependent)
            dependents.update(self.get_all_dependents(dependent))
        return dependents
```

---

## 5. Engineering Task Breakdown

### Phase 1 Task Structure

```
WEEK 1-2: Foundation (Semantic Annotations)
  ├─ T1.1: Refactor existing DaxAstParser for annotations
  ├─ T1.2: Add semantic annotation to AST nodes
  ├─ T1.3: Implement aggregation target detection
  └─ T1.4: Unit test coverage (50+ tests)

WEEK 2-3: Context Understanding (Semantic Analysis)
  ├─ T2.1: Implement SemanticContextAnalyzer
  ├─ T2.2: Build semantic intent classification
  ├─ T2.3: Implement time intelligence detection
  └─ T2.4: Unit test coverage (40+ tests)

WEEK 3-4: Translation Expansion (DAX → SQL)
  ├─ T3.1: Implement Tier 2 arithmetic patterns
  ├─ T3.2: Implement Tier 3 time intelligence translation
  ├─ T3.3: Implement simple Tier 4 CALCULATE/FILTER/ALL
  ├─ T3.4: Add LLM fallback mechanism
  └─ T3.5: Integration test coverage (50+ tests)

WEEK 4-5: Validation Infrastructure (Parity Framework)
  ├─ T4.1: Implement ParityValidationEngine
  ├─ T4.2: Build Fabric execution runner
  ├─ T4.3: Build Snowflake execution runner
  ├─ T4.4: Implement result comparison logic
  └─ T4.5: Unit test coverage (30+ tests)

WEEK 5-6: Dependency Management (Graph Building)
  ├─ T5.1: Implement SemanticDependencyGraph
  ├─ T5.2: Implement cycle detection algorithm
  ├─ T5.3: Implement topological sort
  ├─ T5.4: Validate graph integrity
  └─ T5.5: Unit test coverage (25+ tests)

WEEK 5-6: M Query Support (Parallel)
  ├─ T6.1: Implement M Query parser (simple)
  ├─ T6.2: Implement SELECT pattern translation
  ├─ T6.3: Implement FILTER pattern translation
  ├─ T6.4: Implement transform detection
  └─ T6.5: Unit test coverage (20+ tests)

WEEK 6-8: Testing & Validation (Parallel)
  ├─ T7.1: Build golden dataset (250+ test measures)
  ├─ T7.2: Generate parity test cases (3000+ scenarios)
  ├─ T7.3: Establish regression baseline
  ├─ T7.4: Run full parity validation
  ├─ T7.5: Document parity results
  └─ T7.6: Fix identified issues

WEEK 8: Wrap-up & Handoff
  ├─ T8.1: Performance profiling
  ├─ T8.2: Documentation (module READMEs)
  ├─ T8.3: Demo preparation
  ├─ T8.4: Phase 2 transition planning
  └─ T8.5: Team retrospective
```

---

## 6. Recommended Implementation Order

### 6.1 Critical Path (Sequential, Blocking)

```
Step 1: Enhanced DAX Parser (Weeks 1-2)
  └─ BLOCKER FOR: All subsequent translation work
  └─ Owner: DAX Engineer (1 person)
  └─ Output: Annotated AST with semantic metadata

Step 2: Semantic Context Analyzer (Weeks 2-3)
  └─ BLOCKER FOR: Translation engine, parity validation
  └─ DEPENDS ON: Step 1
  └─ Owner: Semantic Engineer (1 person)
  └─ Output: SemanticIntent classification

Step 3: DAX Translation Engine (Weeks 3-4)
  └─ BLOCKER FOR: Parity validation
  └─ DEPENDS ON: Steps 1-2
  └─ Owner: DAX Engineer + SQL Engineer (2 people)
  └─ Output: SQL translation with metadata

Step 4: Parity Validation Engine (Weeks 4-5)
  └─ BLOCKER FOR: Validation suite
  └─ DEPENDS ON: Step 3
  └─ Owner: QA/Validation Engineer (1 person)
  └─ Output: Parity comparison capability
```

### 6.2 Parallelizable Workstreams (Can run in parallel)

```
Parallel to Steps 1-3:
  ├─ Semantic Dependency Graph (Weeks 5-6)
  │  └─ Owner: Infrastructure Engineer
  │  └─ Dependencies: Step 2 (semantic analysis)
  │
  ├─ M Query Transformer (Weeks 5-6)
  │  └─ Owner: SQL Engineer #2
  │  └─ Dependencies: None (independent)
  │
  └─ Golden Dataset & Test Cases (Weeks 6-8)
     └─ Owner: QA/Validation Engineer
     └─ Dependencies: Step 4 (parity framework)
```

### 6.3 Implementation Sequence (Week-by-Week)

**Week 1:**
- T1.1: Refactor existing parser
- T1.2: Add AST annotations
- *Parallel:* T6.1: Start M Query parser skeleton

**Week 2:**
- T1.3: Aggregation target detection
- T1.4: Parser unit tests
- T2.1: Start SemanticContextAnalyzer
- *Parallel:* T5.1: Start SemanticDependencyGraph skeleton

**Week 3:**
- T2.1-2.3: Complete SemanticContextAnalyzer
- T2.4: Unit tests for analyzer
- T3.1: Start Tier 2 translation patterns
- *Parallel:* T6.2-6.3: M Query translation

**Week 4:**
- T3.1-3.3: Complete DAX translation patterns
- T3.4: LLM fallback integration
- T3.5: Integration tests
- *Parallel:* T5.2-5.3: Graph algorithms
- *Parallel:* T6.4-6.5: M Query testing

**Week 5:**
- T4.1-4.4: Build ParityValidationEngine
- T4.5: Unit tests
- T5.4-5.5: Validate graph integrity
- *Parallel:* T7.1: Build golden dataset

**Week 6:**
- T7.2: Generate test cases
- T7.3: Establish baseline
- (Parallel T4-5 wrap-up)

**Week 7:**
- T7.4: Run full parity validation
- T7.5: Analyze results
- T7.6: Fix identified issues

**Week 8:**
- T8.1-8.5: Profiling, docs, demo, retrospective

---

## 7. Validation & Testing Strategy

### 7.1 Unit Test Coverage (Minimum)

**Parser Tests (50+ tests)**
```python
test_parse_simple_aggregation()
test_parse_measure_reference()
test_parse_calculate_with_filter()
test_parse_all_modifier()
test_parse_time_intelligence_function()
test_semantic_annotation_accuracy()
test_aggregation_target_detection()
test_complexity_tier_assignment()
```

**Context Analyzer Tests (40+ tests)**
```python
test_infer_aggregation_type_sum()
test_infer_aggregation_type_count_distinct()
test_detect_time_intelligence_totalytd()
test_identify_relationship_requirements()
test_detect_row_context_need()
test_semantic_intent_confidence_level()
```

**Translation Engine Tests (50+ tests)**
```python
test_translate_sum_to_sql()
test_translate_count_distinct_to_sql()
test_translate_arithmetic_expression()
test_translate_calculate_with_override()
test_translate_filter_modifier()
test_translate_all_modifier()
test_translate_time_intelligence_window()
test_fallback_to_llm_on_unsupported()
```

**Parity Validation Tests (30+ tests)**
```python
test_execute_in_fabric()
test_execute_in_snowflake()
test_compare_identical_results()
test_compare_with_tolerance()
test_detect_parity_mismatch()
test_generate_parity_report()
```

**Dependency Graph Tests (25+ tests)**
```python
test_build_graph_simple_dependencies()
test_detect_circular_dependency()
test_topological_sort_ordering()
test_get_all_dependents()
test_validate_graph_integrity()
```

**M Query Tests (20+ tests)**
```python
test_parse_simple_select()
test_translate_select_to_sql()
test_translate_filter_predicate()
test_mark_unsupported_custom_function()
test_mark_unsupported_conditional()
```

**Total: ~215 unit tests**

### 7.2 Integration Tests (Phase 1 Test Suite)

**Test Categories:**

1. **End-to-End Translation (50+ tests)**
   ```
   Input: DAX measure from Fabric
   Process: Parse → Analyze → Translate
   Output: Snowflake SQL
   Validation: SQL is syntactically valid
   ```

2. **Parity Validation (100+ tests)**
   ```
   Input: DAX + SQL + test data
   Process: Execute both, compare results
   Output: Parity report
   Validation: Results match within tolerance
   ```

3. **Dependency Graph (30+ tests)**
   ```
   Input: OSI model with measures
   Process: Build graph, detect cycles, sort
   Output: Materialization order
   Validation: Order is correct
   ```

4. **Real Model Sync (50+ tests)**
   ```
   Input: Real Fabric semantic models (5-10)
   Process: Full translation pipeline
   Output: Snowflake semantic views
   Validation: Parity >98%
   ```

**Total: ~230 integration tests**

### 7.3 Golden Dataset Strategy

**Dataset Composition:**

```
Golden Measures (250 total):
├─ Tier 1 Patterns (50 measures)
│  ├─ SUM variations (10)
│  ├─ COUNT variations (10)
│  ├─ DISTINCTCOUNT variations (10)
│  ├─ AVERAGE/MIN/MAX (10)
│  └─ Type variations (10)
│
├─ Tier 2 Patterns (50 measures)
│  ├─ Arithmetic combinations (20)
│  ├─ IF/IFERROR (10)
│  ├─ DIVIDE (10)
│  └─ SWITCH (10)
│
├─ Tier 3 Patterns (50 measures)
│  ├─ TOTALYTD (10)
│  ├─ TOTALMTD (5)
│  ├─ SAMEPERIODLASTYEAR (10)
│  ├─ PREVIOUSYEAR/MONTH (10)
│  └─ DATEADD (15)
│
├─ Tier 4 Patterns (50 measures)
│  ├─ CALCULATE with FILTER (15)
│  ├─ CALCULATE with ALL (15)
│  ├─ CALCULATE with ALLEXCEPT (10)
│  └─ Complex combinations (10)
│
└─ Edge Cases (50 measures)
   ├─ NULL handling (10)
   ├─ Type coercion (10)
   ├─ Measure dependencies (15)
   ├─ Relationship-aware (10)
   └─ Multi-table scenarios (5)
```

**Test Context Generation (3000+ test scenarios):**

```
For each measure:
├─ No filters (baseline)
├─ Single column filters (5 variations)
├─ Multi-column filters (10 combinations)
├─ Edge cases (NULL, zero, negative)
├─ Time-based filters (if applicable)
└─ Relationship-aware filters (5 scenarios)

Example: Total Sales measure
├─ No filter: all orders
├─ Region = "USA": USA orders only
├─ Year = 2024: 2024 orders only
├─ Year = 2024 AND Region = "USA": combination
├─ NULL handling: orders with NULL amount
├─ YTD calculation: cumulative through period
└─ Multi-table: with customer dimension filters
```

### 7.4 Regression Framework

**Baseline Establishment (Week 6):**
```
1. Run parity validation on all 250 golden measures
2. Record results: pass/fail, deviation %, diagnostics
3. Store baseline in version control: `tests/phase1_baseline.json`
4. Establish "golden" as regression reference
```

**Regression Detection (Ongoing):**
```
On any code change:
1. Re-run parity validation
2. Compare to baseline
3. Flag any regressions (measure that was passing now failing)
4. Prevent merge if regressions detected
5. Require root cause analysis before override
```

**Implementation:**
```python
class RegressionFramework:
    def establish_baseline(self):
        baseline = {}
        for measure in golden_measures:
            report = self.validate_measure(measure)
            baseline[measure.name] = {
                'parity_score': report.parity_score,
                'pass_rate': report.pass_rate,
                'status': report.overall_status
            }
        self.save_baseline(baseline)
    
    def detect_regressions(self):
        current = {}
        regressions = []
        for measure in golden_measures:
            report = self.validate_measure(measure)
            current[measure.name] = {
                'parity_score': report.parity_score,
                'pass_rate': report.pass_rate,
                'status': report.overall_status
            }
            baseline = self.load_baseline(measure.name)
            if current[measure.name]['status'] != baseline['status']:
                regressions.append(measure.name)
        if regressions:
            raise RegressionError(f"Regressions: {regressions}")
```

---

## 8. DAX Tier Expansion Plan

### 8.1 Tier Prioritization (Highest Impact First)

**Tier 1: Direct Aggregations** ✅ (Already works, Phase 1 validates)
```
Patterns: SUM([X]), COUNT([Y]), DISTINCTCOUNT([Z]), AVG([A]), MIN([B]), MAX([C])
Effort: 0 (exists)
Coverage: ~20% of real measures
Implementation: Validation only
```

**Tier 2: Simple Arithmetic** ⚠️ (Phase 1: Improve)
```
Patterns:
  - Measure arithmetic: [M1] + [M2], [M1] * 1.1
  - CALCULATE wrapper: CALCULATE([M1])
  - IF/IFERROR: IF([X] > 0, [Y], [Z])
  - DIVIDE: DIVIDE([Revenue], [Units])

Effort: 1.5 weeks (2-3 new patterns per engineer)
Coverage: ~40% of real measures
Implementation Priority: HIGH (biggest ROI)
Expected Success: 95%+

Tasks:
  - T3.1a: Implement arithmetic expression translation
  - T3.1b: Implement IF/IFERROR/SWITCH patterns
  - T3.1c: Implement DIVIDE with null handling
  - T3.1d: Test coverage + edge cases
```

**Tier 3: Time Intelligence** ⚠️ (Phase 1: Complete)
```
Patterns:
  - TOTALYTD, TOTALMTD, TOTALQTD
  - SAMEPERIODLASTYEAR
  - PREVIOUSYEAR, PREVIOUSMONTH, PREVIOUSQUARTER
  - DATEADD

Effort: 1 week (window functions)
Coverage: ~15% of real measures
Implementation Priority: HIGH (important semantic)
Expected Success: 90%+

Tasks:
  - T3.2a: Detect date columns and calendar
  - T3.2b: Generate window function SQL
  - T3.2c: Handle YTD/MTD/QTD boundaries
  - T3.2d: Test against Fabric YTD calculations
  - T3.2e: Document calendar assumptions
```

**Tier 4: Complex CALCULATE** ⚠️ (Phase 1: Simple cases only)
```
Patterns (SUPPORTED in Phase 1):
  - CALCULATE with single FILTER: CALCULATE([M], FILTER(...))
  - CALCULATE with ALL: CALCULATE([M], ALL([Table]))
  - CALCULATE with ALLEXCEPT: CALCULATE([M], ALLEXCEPT(...))
  - Combinations with simple predicates

Patterns (NOT SUPPORTED in Phase 1 - defer to Phase 2):
  - Nested CALCULATE > 3 levels deep
  - Multiple conflicting FILTER clauses
  - Complex ALL/FILTER combinations
  - CALCULATE with USERELATIONSHIP

Effort: 1.5 weeks
Coverage: ~15% of real measures (simple cases)
Implementation Priority: MEDIUM (many edge cases)
Expected Success: 80%+

Tasks:
  - T3.3a: Parse CALCULATE structure
  - T3.3b: Translate simple FILTER to WHERE
  - T3.3c: Translate ALL to context reset
  - T3.3d: Translate ALLEXCEPT to PARTITION BY
  - T3.3e: Flag complex patterns for LLM
  - T3.3f: Test coverage
```

**Tier 5: Complex & Unsupported** ❌ (Phase 1: LLM Fallback + Error)
```
Patterns:
  - SUMX, AVERAGEX, COUNTX (iterators)
  - RANKX (ranking)
  - EARLIER, EARLIEST (row context)
  - Custom DAX functions
  - USERELATIONSHIP (dynamic relationships)

Effort: N/A (not implementing)
Coverage: ~10% of real measures
Implementation Priority: LOW (defer to Phase 2)
Fallback Strategy: LLM + validation

Handling:
  - T3.4a: Detect Tier 5 patterns
  - T3.4b: Call LLM translator
  - T3.4c: Validate LLM output
  - T3.4d: Block deployment if validation fails
  - T3.4e: Document workarounds
```

### 8.2 Implementation Order (Tier by Tier)

```
Week 3 (T3.1): Tier 2 Arithmetic
  Priority: HIGH (40% coverage)
  Effort: 1.5 weeks (split across 2 engineers)
  Owner: DAX Engineer + SQL Engineer
  Deliverable: ~20 new translation patterns
  
  Step 1: Identify all Tier 2 patterns in test suite (T3.1a)
  Step 2: Implement arithmetic expression translation (T3.1b)
  Step 3: Implement IF/IFERROR/SWITCH (T3.1c)
  Step 4: Implement DIVIDE (T3.1d)
  Step 5: Unit tests (30+ tests)
  Step 6: Integration tests (30+ tests)

Week 3-4 (T3.2): Tier 3 Time Intelligence
  Priority: HIGH (15% coverage, important semantic)
  Effort: 1 week
  Owner: DAX Engineer + SQL Engineer
  Deliverable: Window function generation
  
  Step 1: Detect date columns in model (T3.2a)
  Step 2: Build window function templates (T3.2b)
  Step 3: Handle YTD/MTD boundaries (T3.2c)
  Step 4: Validate against Fabric (T3.2d)
  Step 5: Document assumptions (T3.2e)
  Step 6: Unit tests (20+ tests)
  Step 7: Integration tests (20+ tests)

Week 4-5 (T3.3): Tier 4 Simple CALCULATE
  Priority: MEDIUM (15% coverage, many edge cases)
  Effort: 1.5 weeks
  Owner: DAX Engineer + SQL Engineer
  Deliverable: CALCULATE/FILTER/ALL translation
  
  Step 1: Parse CALCULATE structure (T3.3a)
  Step 2: Translate FILTER patterns (T3.3b)
  Step 3: Translate ALL patterns (T3.3c)
  Step 4: Translate ALLEXCEPT patterns (T3.3d)
  Step 5: Flag complex patterns (T3.3e)
  Step 6: Unit tests (25+ tests)
  Step 7: Integration tests (25+ tests)

Week 3-4 (T3.4): Tier 5 Fallback
  Priority: MEDIUM (needed for coverage, but LLM-based)
  Effort: 1 week
  Owner: DAX Engineer
  Deliverable: LLM integration + fallback handling
  
  Step 1: Detect Tier 5 patterns (T3.4a)
  Step 2: Call LLM translator (T3.4b)
  Step 3: Validate results (T3.4c)
  Step 4: Block on failure (T3.4d)
  Step 5: Documentation (T3.4e)
  Step 6: Unit tests (15+ tests)
```

### 8.3 Tier Coverage Target

**Phase 1 End-State Coverage:**

| Tier | Patterns | Coverage | Expected Success | Effort |
|------|----------|----------|------------------|--------|
| **T1** | Direct agg | 20% | 100% | 0 weeks |
| **T2** | Arithmetic | 40% | 95% | 1.5 weeks |
| **T3** | Time intel | 15% | 90% | 1 week |
| **T4** | CALCULATE | 15% | 80% | 1.5 weeks |
| **T5** | Complex | 10% | 70%* | 1 week (LLM) |
| | | | | |
| **Total** | | **~90%** | **~88%** | **~5 weeks** |

*T5 success rate reflects LLM unreliability; Phase 2 will improve

---

## 9. M Query Translation Scope (Minimal MVP)

### 9.1 Phase 1 M Query Scope (MINIMAL)

**Goal:** Translate 80% of simple M queries to SQL; defer complex cases

**Supported Patterns:**

1. **Simple SELECT (100% coverage target)**
```m
let
    Source = Snowflake.Databases(...),
    Data = Source{[Name="TABLE_NAME"]}[Data]
in
    Data
```
Translation:
```sql
SELECT * FROM source_table;
```

2. **Column Selection (90% coverage)**
```m
Table.SelectColumns(source, {"Col1", "Col2", "Col3"})
```
Translation:
```sql
SELECT "COL1", "COL2", "COL3" FROM source_table;
```

3. **Row Filtering (80% coverage)**
```m
Table.SelectRows(source, each [Year] > 2020)
```
Translation:
```sql
SELECT * FROM source_table WHERE year > 2020;
```

4. **Simple Type Conversion (70% coverage)**
```m
Table.TransformColumns(source, {{"Amount", Currency.From}})
```
Translation:
```sql
SELECT CAST(amount AS NUMERIC) FROM source_table;
```

5. **Column Renaming (85% coverage)**
```m
Table.RenameColumns(source, {{"OldName", "NewName"}})
```
Translation:
```sql
SELECT "OldName" AS "NewName" FROM source_table;
```

### 9.2 NOT Supported in Phase 1 (Explicit Out-of-Scope)

```
❌ Custom M functions
   let f = (x) => x * 2
   in Table.TransformColumns(source, {"Amount", f})

❌ Conditional logic
   let x = if condition then A else B
   in x

❌ Loops / iteration
   Table.AddColumn(source, ...)

❌ External API calls
   Json.Document(...)

❌ Complex transformations
   Table.Group(...), Table.Pivot(...)

❌ Parameter resolution
   Parameters.Start

❌ Error handling
   try/catch logic
```

### 9.3 Fallback Behavior

**When M Query Cannot Be Translated:**

```python
def transform_m_query(m_code: str, mappings: Dict) -> MQueryTransformResult:
    """
    Try to translate M query; fallback to pass-through on failure.
    """
    try:
        # Attempt translation
        if is_simple_select(m_code):
            sql = translate_simple_select(m_code, mappings)
            return MQueryTransformResult(
                sql_expression=sql,
                is_supported=True,
                metadata={"pattern": "simple_select"}
            )
        
        elif is_column_selection(m_code):
            sql = translate_column_selection(m_code, mappings)
            return MQueryTransformResult(sql, True, {"pattern": "select_columns"})
        
        # ... more patterns ...
        
        else:
            # Unsupported pattern
            return MQueryTransformResult(
                sql_expression=None,
                is_supported=False,
                metadata={
                    "pattern": "unsupported",
                    "reason": "M query uses unsupported constructs",
                    "recommendation": "Manually define equivalent SQL view",
                    "m_code_preserved": m_code  # Store original for reference
                }
            )
    
    except Exception as e:
        # Translation error
        return MQueryTransformResult(
            sql_expression=None,
            is_supported=False,
            metadata={
                "pattern": "translation_error",
                "reason": str(e),
                "m_code_preserved": m_code
            }
        )
```

### 9.4 Phase 1 M Query Coverage Target

| Pattern | Coverage | Translation | Fallback |
|---------|----------|------------|----------|
| Simple SELECT | 95% | SQL | Error |
| Column selection | 80% | SQL | Error |
| Row filter | 75% | SQL | Error |
| Type conversion | 60% | SQL | Error |
| Renaming | 85% | SQL | Error |
| **Overall** | **~79%** | Partial | Graceful |

**Minimum Success Criteria:**
- ✅ 70%+ of M queries in test set translate successfully
- ✅ 0% silent failures (failures are obvious)
- ✅ Original M code preserved for reference

---

## 10. Risks During Phase 1

### 10.1 Technical Risks (High Priority)

**Risk 1: Parser Annotation Complexity**
```
Risk: Semantic annotation of AST may require major refactoring
Impact: Could delay entire phase
Probability: MEDIUM

Mitigation:
  - Start with copy of existing parser (don't refactor in-place)
  - Add annotations incrementally
  - Test each annotation independently
  - Keep old parser as fallback
  
Acceptance Criteria:
  - Parser handles all existing test cases
  - Annotations don't break existing code
```

**Risk 2: Parity Comparison Unreliability**
```
Risk: Fabric API calls may be flaky or unavailable
Impact: Cannot validate translations
Probability: MEDIUM

Mitigation:
  - Use cached Fabric results for validation (don't live-call)
  - Implement retry logic with exponential backoff
  - Fallback to stub results if needed
  - Use mock Fabric for unit tests
  
Acceptance Criteria:
  - 90%+ of Fabric calls succeed
  - Validation completes within 10 min for 250 measures
```

**Risk 3: Time Intelligence Calendar Detection**
```
Risk: Cannot reliably detect calendar type (calendar vs fiscal)
Impact: YTD calculations may use wrong boundaries
Probability: MEDIUM

Mitigation:
  - Require explicit calendar type in model metadata
  - Default to gregorian calendar
  - Validate YTD boundaries against Fabric results
  - Flag ambiguous cases for manual review
  
Acceptance Criteria:
  - 100% of models have explicit calendar type
  - YTD calculations match Fabric within <1% deviation
```

**Risk 4: Measure Dependency Cycles Not Detected**
```
Risk: Circular measure dependencies crash topological sort
Impact: Model deployment fails with unclear error
Probability: LOW (cycle detection exists)

Mitigation:
  - Implement robust cycle detection
  - Provide clear error message with cycle path
  - Block deployment with helpful suggestion
  - Link to documentation
  
Acceptance Criteria:
  - All cycles detected (100% recall)
  - Clear error messages
  - User can understand and fix problem
```

### 10.2 Architectural Risks

**Risk 5: LLM Translation Non-Determinism**
```
Risk: LLM may produce different SQL for same DAX on different calls
Impact: Translation results not reproducible; validation fails
Probability: HIGH

Mitigation:
  - Cache all LLM translations (don't re-call)
  - Pin LLM version/model
  - Always validate against Fabric
  - Block deployment if validation fails
  - Log all LLM calls for debugging
  
Acceptance Criteria:
  - LLM used <10% of time (deterministic covers 90%+)
  - All LLM outputs validated
  - 0% silent failures
```

**Risk 6: SQL Dialect Incompatibility**
```
Risk: Snowflake SQL syntax may differ from assumptions
Impact: Generated SQL fails to execute
Probability: MEDIUM

Mitigation:
  - Test all SQL patterns on real Snowflake
  - Use Snowflake functions (not ANSI SQL)
  - Handle Snowflake case-sensitivity
  - Document dialect assumptions
  - Include Snowflake-specific syntax tests
  
Acceptance Criteria:
  - All generated SQL is valid Snowflake
  - No syntax errors in 100+ test cases
  - Queries execute and return correct results
```

### 10.3 Performance Risks

**Risk 7: Translation Pipeline Too Slow**
```
Risk: Translating 1000 measures takes >10 minutes
Impact: User experience poor; sync times unacceptable
Probability: MEDIUM

Mitigation:
  - Profile translation pipeline early
  - Optimize bottlenecks (parser, analyzer, generation)
  - Use async execution for I/O
  - Implement caching (Phase 2)
  - Document performance expectations
  
Acceptance Criteria:
  - 1000 measures translate in <5 minutes
  - Average per-measure: <300ms
  - Parser: <50ms per measure
  - Analyzer: <100ms per measure
  - Translator: <50ms per measure
  - Validation: <100ms per measure
```

**Risk 8: Parity Validation Too Expensive**
```
Risk: Validating 250 measures against Fabric takes >30 minutes
Impact: Validation too slow to run frequently
Probability: MEDIUM

Mitigation:
  - Use representative sample (not all measures)
  - Run Fabric queries in parallel (if possible)
  - Cache results (don't re-execute)
  - Implement incremental validation
  - Use async/await for I/O
  
Acceptance Criteria:
  - 250 measures validated in <10 minutes
  - Average per-measure: <2.4 seconds
  - Can run validation per commit
```

### 10.4 Validation Risks

**Risk 9: False Parity Positives**
```
Risk: Validation says measures match, but Snowflake produces wrong results
Impact: Silent data corruption; data loss; user distrust
Probability: LOW (but critical)

Mitigation:
  - Validate on real data (not synthetic)
  - Compare exact values (not just aggregates)
  - Test multiple filter contexts
  - Implement row-level parity tests
  - Manual review of all Phase 1 measures
  
Acceptance Criteria:
  - 0% false positives (measures that fail in production)
  - 100% of measures manually spot-checked
  - Parity threshold: 99.9% match
```

**Risk 10: Golden Dataset Not Representative**
```
Risk: Test measures don't cover real-world patterns
Impact: Translation works on tests but fails on real models
Probability: MEDIUM

Mitigation:
  - Use real Fabric models (not synthetic)
  - Include diverse patterns (5-10 different models)
  - Include edge cases (NULL, zero, negative)
  - Include multi-table scenarios
  - Include complex relationships
  
Acceptance Criteria:
  - 250 golden measures from real models
  - Covers all identified patterns
  - Covers edge cases
  - Can be extended with customer models
```

---

## 11. Phase 1 Success Criteria

### 11.1 Engineering Completion Metrics

**Translation Coverage:**
- ✅ **90%+** of real-world measures translate successfully
- ✅ **95%** of Tier 1-2 measures translate deterministically
- ✅ **85%** of Tier 3 measures translate correctly
- ✅ **70%** of Tier 4 measures translate correctly
- ✅ **<10%** of measures require LLM fallback
- ❌ **0%** unsupported pattern errors (must be graceful)

**Parity Validation:**
- ✅ **98%+** parity score on golden dataset
- ✅ **<0.1%** average deviation for aggregations
- ✅ **99%** of test cases pass validation
- ✅ **0%** false positives (incorrect passing measures)
- ✅ **95%+** precision in mismatch detection

**Performance:**
- ✅ Parse 1000 measures: **<50 seconds** (50ms each)
- ✅ Analyze 1000 measures: **<100 seconds** (100ms each)
- ✅ Translate 1000 measures: **<100 seconds** (100ms each)
- ✅ Validate 250 measures: **<10 minutes** (2.4s each)
- ✅ Total pipeline: **<5 minutes** for model of 1000 measures

**Regression Prevention:**
- ✅ **100%** of baseline tests pass
- ✅ **0%** regressions detected on code changes
- ✅ **Automated** regression detection before merge

**Code Quality:**
- ✅ **>80%** test coverage on Phase 1 modules
- ✅ **215+** unit tests passing
- ✅ **230+** integration tests passing
- ✅ **0** critical bugs (blocking deployment)
- ✅ **<5** medium bugs (deferred to Phase 2)
- ✅ Module READMEs documented
- ✅ Key algorithms documented

### 11.2 Deliverable Checkpoints

**Week 2 Checkpoint (Parser + Analyzer):**
- [ ] SemanticAstParser implemented and tested
- [ ] AnnotatedAstNode generating semantic metadata
- [ ] SemanticContextAnalyzer working on Tier 1-2 patterns
- [ ] 50+ parser tests passing
- [ ] 40+ analyzer tests passing

**Week 4 Checkpoint (Translation Engine):**
- [ ] Tier 2 arithmetic patterns implemented
- [ ] Tier 3 time intelligence implemented
- [ ] Simple Tier 4 CALCULATE patterns implemented
- [ ] 50+ translation tests passing
- [ ] Integration tests running successfully

**Week 5 Checkpoint (Validation Framework):**
- [ ] ParityValidationEngine functional
- [ ] Fabric execution runner working
- [ ] Snowflake execution runner working
- [ ] Comparison logic accurate
- [ ] 30+ validation tests passing

**Week 6 Checkpoint (Dependency Graph + M Query):**
- [ ] SemanticDependencyGraph implemented
- [ ] Cycle detection working
- [ ] Topological sort correct
- [ ] M Query transformer basic patterns working
- [ ] 25+ graph tests passing
- [ ] 20+ M Query tests passing

**Week 8 Checkpoint (Full Validation):**
- [ ] Golden dataset established (250+ measures)
- [ ] Parity validation framework complete
- [ ] 90%+ translation coverage achieved
- [ ] 98%+ parity score on golden dataset
- [ ] Regression framework in place
- [ ] Performance targets met
- [ ] All documentation complete
- [ ] Team ready for Phase 2

### 11.3 Go/No-Go Decision Point (End of Week 8)

**Phase 1 is COMPLETE if:**
- ✅ 90%+ translation coverage achieved
- ✅ 98%+ parity score on golden dataset
- ✅ All 215+ unit tests passing
- ✅ All 230+ integration tests passing
- ✅ 0 critical bugs
- ✅ Performance targets met
- ✅ Team consensus (no major concerns)

**Phase 1 is NOT COMPLETE if:**
- ❌ <85% translation coverage
- ❌ <95% parity score
- ❌ Critical bugs (data loss, silent failures)
- ❌ Fundamental architectural issues discovered

**If NOT COMPLETE:**
- Extend Phase 1 by 2-4 weeks
- Focus on highest-impact gaps
- Defer Tier 4-5 to Phase 2 if needed

---

## 12. Recommended Team Structure

### 12.1 Ideal Team Composition

**Team Size:** 4 people (3.5 FTE recommended; 3 FTE minimum)

**Roles & Responsibilities:**

**Role 1: DAX Translation Engineer (1.5 FTE)**
```
Ownership:
  - SemanticAstParser enhancement
  - DaxTranslationEngine implementation
  - Tier 2-4 DAX pattern translation
  - LLM integration for Tier 5
  - DAX translation testing

Required Skills:
  - Strong Python expertise
  - Understanding of DAX semantics
  - AST/parser knowledge
  - Regex and pattern matching
  - Testing & debugging

Key Deliverables:
  - Enhanced parser with annotations
  - Tier 2-4 translation patterns
  - 50+ integration tests
  - Translation quality >85%
```

**Role 2: SQL/Snowflake Engineer (1 FTE)**
```
Ownership:
  - SQL generation from translated DAX
  - Snowflake dialect expertise
  - M Query transformer
  - Performance optimization
  - SQL testing & validation

Required Skills:
  - Advanced SQL (Snowflake)
  - Query optimization
  - DDL/DML generation
  - Performance profiling
  - Debugging SQL issues

Key Deliverables:
  - DaxTranslationEngine SQL generation
  - M Query transformer
  - SQL validation tests
  - Performance benchmarking
```

**Role 3: QA/Validation Engineer (1 FTE)**
```
Ownership:
  - ParityValidationEngine
  - Test case generation
  - Golden dataset creation
  - Regression framework
  - Validation testing

Required Skills:
  - Test infrastructure expertise
  - Automation testing
  - Data validation
  - Python testing frameworks
  - Metrics/reporting

Key Deliverables:
  - Parity validation framework
  - Golden dataset (250+ measures)
  - Test case generator (3000+ scenarios)
  - Regression detection
  - Validation reports
```

**Role 4: Infrastructure/Graph Engineer (0.5 FTE)**
```
Ownership:
  - SemanticDependencyGraph
  - Cycle detection algorithms
  - Topological sorting
  - Graph visualization support
  - Performance optimization

Required Skills:
  - Graph algorithms
  - Python data structures
  - Performance optimization
  - Algorithm analysis

Key Deliverables:
  - SemanticDependencyGraph implementation
  - Cycle detection
  - Topological sort
  - Graph tests (25+)

Note: Can be part-time; can be absorbed by other engineers if needed
```

### 12.2 Ownership Boundaries

| Module | Owner | Review |
|--------|-------|--------|
| SemanticAstParser | DAX Engineer | SQL Engineer |
| SemanticContextAnalyzer | DAX Engineer | SQL Engineer |
| DaxTranslationEngine | DAX Engineer + SQL Engineer | Both |
| MQueryTransformer | SQL Engineer | DAX Engineer |
| ParityValidationEngine | QA Engineer | DAX + SQL Engineer |
| SemanticDependencyGraph | Infrastructure Engineer | DAX Engineer |
| Test Suite | QA Engineer | All |
| Documentation | All (shared) | Tech Lead |

### 12.3 Review Responsibilities

**Code Review Process:**
```
Standard PR workflow:
  1. Feature branch created
  2. Developer works on task
  3. Local testing + unit tests
  4. Create PR with description
  5. Code review by 2+ people:
     - Primary owner (deep review)
     - Secondary reviewer (architecture/style)
  6. Automated tests must pass (100%+)
  7. Merge on approval
  8. CI/CD pipeline runs (integration tests)
```

**Architecture Reviews (Weekly):**
```
Every Monday morning (1 hour):
  - Share progress updates
  - Discuss blockers
  - Review design decisions
  - Align on priorities
  - Identify risks early
```

**Parity Validation Gate:**
```
Before any merge to main:
  - Must run full parity validation
  - Must have 98%+ parity score
  - Must have 0 new regressions
  - QA Engineer final approval
```

---

## 13. Final Execution Recommendation

### 13.1 What to Implement Immediately (Week 1)

**Start Right Now:**

1. **Setup & Infrastructure (1 day)**
   - Create Phase 1 branch from develop
   - Create module directory structure
   - Setup testing framework (pytest)
   - Configure CI/CD for Phase 1
   - Create module skeletons

2. **Begin SemanticAstParser (Days 2-10)**
   - Create `semantic_ast_parser.py` as copy of existing parser
   - Add AnnotatedAstNode dataclass
   - Implement aggregation target detection
   - Write first 20 parser unit tests
   - Get existing tests passing

3. **Prototype ParityValidationEngine (Days 5-10)**
   - Setup Fabric API client (mock if needed)
   - Setup Snowflake connector
   - Implement ExecutionRunner skeleton
   - Implement comparison logic
   - Write proof-of-concept validation

### 13.2 What to Prototype First (Week 2)

**Proof-of-Concept Tasks:**

1. **Parser Annotation POC**
   - Take 5 sample DAX expressions
   - Manually parse + annotate them
   - Verify semantic metadata is correct
   - Identify edge cases

2. **Semantic Intent Classification POC**
   - Implement SemanticContextAnalyzer skeleton
   - Classify 10 sample measures
   - Verify classifications are correct
   - Identify missing patterns

3. **SQL Generation POC**
   - Implement DaxTranslationEngine skeleton
   - Generate SQL for 10 sample measures
   - Validate SQL is syntactically correct
   - Identify gaps

### 13.3 What to Defer to Phase 2

**Explicitly NOT Implementing:**

1. **LLM Optimization**
   - Caching translations (Phase 2)
   - Batch API calls (Phase 2)
   - Cost optimization (Phase 2)

2. **Complex DAX Patterns**
   - RANKX (Phase 2)
   - EARLIER/EARLIEST (Phase 2)
   - Complex nested CALCULATE (Phase 2)

3. **M Query Advanced Support**
   - Custom functions (Phase 2)
   - Conditional logic (Phase 2)
   - Complex transformations (Phase 2)

4. **Optimization**
   - Parallel translation (Phase 2)
   - Incremental sync (Phase 2)
   - Query result caching (Phase 2)

5. **Enterprise Features**
   - RLS integration (Phase 2+)
   - Metadata governance (Phase 2+)
   - Audit logging (Phase 2+)

### 13.4 Realistic Phase 1 Timeline

**Expected Schedule (8 Weeks):**

```
Week 1: Foundation
  - Setup infrastructure
  - SemanticAstParser started
  - Team alignment on approach
  - Estimated delivery: Parser skeleton working

Week 2: Parser + Analyzer
  - SemanticAstParser annotations complete
  - SemanticContextAnalyzer started
  - First integration tests passing
  - Estimated delivery: Parser + Analyzer POC working

Week 3: Translation Engine
  - DaxTranslationEngine skeleton
  - Tier 1-2 patterns implemented
  - M Query transformer started
  - Estimated delivery: Tier 1-2 working, 50+ tests passing

Week 4: Time Intelligence + Tier 4
  - Tier 3 time intelligence complete
  - Simple Tier 4 CALCULATE patterns
  - 100+ integration tests passing
  - Estimated delivery: 85%+ translation coverage

Week 5: Validation Framework
  - ParityValidationEngine complete
  - Fabric + Snowflake execution runners
  - SemanticDependencyGraph started
  - Estimated delivery: Parity framework working

Week 6: Dependency Graph + Testing
  - SemanticDependencyGraph complete
  - M Query transformer complete
  - Golden dataset established (250 measures)
  - Estimated delivery: All modules complete

Week 7: Validation Running
  - Parity validation running on all measures
  - Issues identified and fixed
  - Regression framework in place
  - Estimated delivery: 98%+ parity on golden set

Week 8: Handoff Preparation
  - Performance profiling
  - Documentation complete
  - Team training
  - Phase 2 planning
  - Estimated delivery: Phase 1 complete, ready for handoff
```

### 13.5 Realistic Phase 1 Completion Expectations

**What WILL be delivered:**
- ✅ 90%+ DAX translation coverage
- ✅ 98%+ parity on golden dataset
- ✅ Semantic intent classification working
- ✅ Parity validation framework
- ✅ Dependency graph with cycle detection
- ✅ Basic M Query transformation (80%)
- ✅ 215+ unit tests
- ✅ 230+ integration tests
- ✅ Performance metrics collected
- ✅ Comprehensive documentation

**What WON'T be delivered:**
- ❌ Tier 5 complex DAX patterns (LLM fallback only)
- ❌ RANKX, EARLIER, USERELATIONSHIP
- ❌ Translation caching/parallelization
- ❌ RLS integration
- ❌ Audit logging
- ❌ Performance optimization beyond profiling
- ❌ Enterprise deployment playbook

**What MIGHT be delivered (if ahead of schedule):**
- 🔄 RANKX basic support (if simple enough)
- 🔄 Translation caching (if time permits)
- 🔄 Parallel translation (if time permits)
- 🔄 Additional M Query patterns (if time permits)

---

## 14. Appendix: Task Template

### Task Template (Use for All Phase 1 Tasks)

```
TASK: [Task ID] [Task Name]
STATUS: Not Started
ASSIGNEE: [Engineer Name]
OWNER REVIEW: [Reviewer]
DEPENDENCIES: [Blocking tasks]
ESTIMATED EFFORT: [Days]

OBJECTIVE:
[Clear, specific objective. What does "done" mean?]

ACCEPTANCE CRITERIA:
- [ ] Acceptance criterion 1
- [ ] Acceptance criterion 2
- [ ] Unit tests pass (specify count)
- [ ] Integration tests pass (if applicable)
- [ ] Code reviewed and approved
- [ ] Documentation updated

INPUTS:
- [Data/artifacts needed]

OUTPUTS:
- [Deliverables]

IMPLEMENTATION NOTES:
[Specific implementation guidance, patterns to follow, gotchas]

TESTING STRATEGY:
- Unit tests: [describe]
- Integration tests: [describe]
- Edge cases: [describe]

RISKS:
- Risk 1: [mitigation]
- Risk 2: [mitigation]

SUCCESS METRICS:
- [Measurable metric 1]
- [Measurable metric 2]
```

---

## Conclusion

**Phase 1 is an 8-week execution plan focused on:**

1. **Building core translation infrastructure** (Parser → Analyzer → Translator)
2. **Establishing validation framework** (Parity comparison, regression detection)
3. **Achieving 90%+ DAX coverage** (Tiers 1-4, simple cases)
4. **Delivering production-quality foundation** (215+ unit tests, comprehensive validation)

**Critical Success Factor:** Parity validation must work perfectly. If validation is unreliable, the entire initiative fails.

**Next Step:** Assemble team, review this plan, approve Phase 1 scope, and begin Week 1 tasks immediately.

---

**Document Status:** Ready for Execution  
**Last Updated:** May 11, 2026  
**Next Review:** Start of Week 2 (planning checkpoint)
