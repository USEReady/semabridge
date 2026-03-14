# Measure Pipeline Improvement Action Plan

## Overview

The SemaBridge measure pipeline is now robust for simple cases but needs enhancements for complex DAX expressions. This document outlines specific improvements to achieve production-grade reliability.

---

## Priority 1: Time Intelligence Support

### Current State
- 10 measures (18.5%) use time intelligence functions
- Currently flagged but not translated
- User sees: "requires manual override"

### Implementation Plan

#### Step 1: Detect Time Intelligence Functions
```python
# src/semabridge/converter/dax_translator.py

TIME_INTEL_FUNCTIONS = {
    "TOTALYTD": "Year-to-Date",
    "TOTALMTD": "Month-to-Date",
    "TOTALQTD": "Quarter-to-Date",
    "SAMEPERIODLASTYEAR": "Prior Year",
    "PARALLELPERIOD": "Period Offset",
}

def detect_time_intelligence(dax: str) -> Optional[str]:
    """Identify which time intel function is used."""
    for func in TIME_INTEL_FUNCTIONS.keys():
        if func.upper() in dax.upper():
            return func
    return None
```

#### Step 2: Generate Snowflake Window Functions
```sql
-- Input DAX:
-- TOTALYTD(SUM('SalesFact'[Revenue]), 'Date'[Date])

-- Output SQL (Year-to-Date):
SUM(salesfact."Revenue") OVER (
    PARTITION BY YEAR(date."Date")
    ORDER BY date."Date"
    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
)

-- Output SQL (Same Period Last Year):
(
    SELECT SUM(SUM(salesfact."Revenue")) 
    OVER (
        PARTITION BY YEAR(DATEADD(YEAR, -1, date."Date"))
        ORDER BY DATEADD(YEAR, -1, date."Date")
    )
)
```

#### Step 3: Estimated Impact
- Measures enabled: 10 additional (18.5%)
- Success rate: 20.4% → 39% (11/54 → 21/54)
- Complexity: Medium (window function generation)
- Estimated effort: 2-3 sprints

---

## Priority 2: Complex DAX Pattern Support

### Current State
- 21 measures (39%) fail due to complex DAX
- Examples: DIVIDE, nested IF, CALCULATE with complex filters
- User sees: "DAX translation failed (Tier 4)"

### Implementation Plan

#### Step 1: Expand Pattern Recognition
```python
# New patterns to support:
PATTERN_TEMPLATES = {
    "simple_divide": r"^\s*DIVIDE\s*\(\s*(\[.+?\])\s*,\s*(\[.+?\])\s*\)\s*$",
    "safe_divide": r"^\s*DIVIDE\s*\(\s*(\[.+?\])\s*,\s*(\[.+?\])\s*,\s*(\d+)\s*\)\s*$",
    "complex_if": r"IF\s*\(.+?,\s*.+?,\s*.+?\)",
    "nested_calculate": r"CALCULATE\s*\(.+?\s*FILTER\s*\(.+?\)\s*\)",
}
```

#### Step 2: Generate Safe SQL
```sql
-- Input: DIVIDE([Revenue], [Units], 0)
-- Output: CASE WHEN units = 0 THEN 0 ELSE revenue / units END

-- Input: IF(Total > 0, Profit / Total, 0)
-- Output: CASE WHEN total > 0 THEN profit / total ELSE 0 END
```

#### Step 3: Estimated Impact
- Measures enabled: 15-18 additional (28-33%)
- Success rate: 39% → 56% (21/54 → 30/54)
- Complexity: High (requires AST parser)
- Estimated effort: 4-5 sprints

---

## Priority 3: LLM-Assisted Translation

### Current State
- 36 measures (66.7%) cannot be auto-translated
- No fallback for complex/unsupported patterns
- Manual overrides required for production

### Implementation Plan

#### Step 1: LLM Integration
```python
# src/semabridge/converter/llm_dax_translator.py

from anthropic import Anthropic

class LLMDAXTranslator:
    def __init__(self):
        self.client = Anthropic()
        
    def translate_complex_dax(self, dax: str, table_alias: str) -> str:
        """Use Claude to translate complex DAX to Snowflake SQL."""
        prompt = f"""
        Convert this DAX expression to Snowflake SQL:
        
        DAX: {dax}
        Table Alias: {table_alias}
        
        Rules:
        - Use Snowflake window functions for time intelligence
        - Use CASE for conditionals
        - Reference columns as alias."ColumnName"
        - Return ONLY the SQL expression, no markdown
        """
        
        response = self.client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        
        return response.content[0].text.strip()
```

#### Step 2: Confidence Scoring
```python
# Validation layer
def validate_llm_translation(original_dax: str, generated_sql: str) -> float:
    """Score LLM output confidence (0.0-1.0)."""
    
    # Check for SQL injection patterns
    dangerous_patterns = ["DROP", "DELETE", "TRUNCATE", "UPDATE", ";--"]
    if any(p in generated_sql.upper() for p in dangerous_patterns):
        return 0.0
    
    # Check for required components
    has_aggregation = any(f in generated_sql.upper() for f in ["SUM", "AVG", "COUNT"])
    has_table_ref = "_" in generated_sql  # alias convention
    
    score = 0.5  # base score
    if has_aggregation: score += 0.3
    if has_table_ref: score += 0.2
    
    return min(1.0, score)
```

#### Step 3: User Confirmation Workflow
```
1. DAX fails auto-translation
2. LLM generates candidate SQL
3. Confidence score: 0.65 → Yellow flag
4. Show to user with original DAX
5. User approves or provides override
6. Store approval for audit trail
```

#### Step 4: Estimated Impact
- Measures enabled: 30+ additional (55%+)
- Success rate: 56% → 75%+ (30/54 → 40+/54)
- Complexity: Medium (API integration + validation)
- Estimated effort: 1-2 sprints

---

## Priority 4: Manual Override Framework

### Current State
- No mechanism for user-provided SQL overrides
- Complex measures stuck in "PARTIAL" status
- Production blocking issue

### Implementation Plan

#### Step 1: Override File Format
```yaml
# metric_overrides.yaml
Competitive Marketing Analysis:
  "SalesFact.Total Units YTD":
    sql: |
      SUM(salesfact."Units") OVER (
          PARTITION BY YEAR(date."Date")
          ORDER BY date."Date"
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
      )
    notes: "Year-to-date aggregation using window function"
    reviewed_by: "data-team"
    reviewed_date: "2026-03-15"
    
  "SalesFact.% Units Market Share":
    sql: |
      CASE 
        WHEN SUM(all_market."Units") = 0 THEN 0
        ELSE ROUND(
            SUM(salesfact."Units") * 100.0 / SUM(all_market."Units"), 
            2
        )
      END
    notes: "Market share percentage with safe division"
```

#### Step 2: Validation Layer
```python
# src/semabridge/core/override_validator.py

def validate_override(metric: SMLMetric, override_sql: str) -> Tuple[bool, str]:
    """Validate user-provided SQL override."""
    
    # Check for SQL injection
    dangerous = check_dangerous_patterns(override_sql)
    if dangerous:
        return False, f"Dangerous SQL patterns detected: {dangerous}"
    
    # Validate column references
    expected_cols = extract_columns(metric.expression)
    actual_cols = extract_columns(override_sql)
    if not expected_cols.issubset(actual_cols):
        return False, f"Missing columns: {expected_cols - actual_cols}"
    
    # Validate aggregation matches
    expected_agg = metric.aggregation.value
    if not any(agg in override_sql.upper() for agg in ["SUM", "AVG", "COUNT", "CASE"]):
        return False, "No aggregation function found"
    
    return True, "Override is valid"
```

#### Step 3: UI Integration
```python
# src/semabridge/api/routes/measures.py

@router.post("/metrics/{metric_id}/override")
async def set_metric_override(metric_id: str, override_request: OverrideRequest) -> Response:
    """Allow users to override metric SQL."""
    
    metric = db.get_metric(metric_id)
    is_valid, message = validate_override(metric, override_request.sql)
    
    if not is_valid:
        return {"status": "error", "message": message}
    
    db.set_metric_override(
        metric_id=metric_id,
        sql=override_request.sql,
        reviewed_by=request.user.id,
        reviewed_at=datetime.utcnow()
    )
    
    return {"status": "success", "metric_id": metric_id}
```

#### Step 4: Estimated Impact
- Measures enabled: User-determined (all remaining 36)
- Success rate: 75% → 100% (if all have overrides)
- Complexity: Low (infrastructure)
- Estimated effort: 1-2 sprints
- Dependencies: Priority 3 LLM should auto-populate most overrides

---

## Implementation Roadmap

### Sprint 1-2: Time Intelligence (Quick Win)
- [ ] Implement TOTALYTD window function
- [ ] Implement SAMEPERIODLASTYEAR window function
- [ ] Test on Competitive Marketing Analysis dataset
- **Result**: 20.4% → 39% success rate

### Sprint 2-3: Complex Pattern Support
- [ ] Expand DIVIDE function support
- [ ] Add IF/CASE expression support  
- [ ] Handle CALCULATE with complex filters
- **Result**: 39% → 56% success rate

### Sprint 3: LLM Integration
- [ ] Anthropic API client setup
- [ ] DAX → SQL prompt engineering
- [ ] Confidence scoring & validation
- [ ] Approval workflow
- **Result**: 56% → 75% success rate

### Sprint 4: Override Framework
- [ ] YAML configuration loading
- [ ] SQL validation engine
- [ ] API endpoints for overrides
- [ ] Audit trail logging
- **Result**: 75% → 95%+ success rate (with user overrides)

---

## Testing Strategy

### Unit Tests
```python
# tests/test_time_intelligence_translator.py
def test_totalytd_translation():
    dax = "TOTALYTD(SUM('SalesFact'[Revenue]), 'Date'[Date])"
    expected = "SUM(salesfact.\"Revenue\") OVER (PARTITION BY YEAR...)"
    assert translate(dax) == expected

# tests/test_llm_translator.py
def test_llm_confidence_scoring():
    sql = "SUM(table.\"Revenue\") / COUNT(*)"
    score = validate_llm_translation(original_dax, sql)
    assert score > 0.6  # Acceptable confidence
```

### Integration Tests
```python
# tests/test_measure_pipeline_full.py
def test_competitive_marketing_dataset():
    """Test on real complex dataset."""
    results = analyze_dataset("Competitive Marketing Analysis")
    assert results.success_rate >= 0.75  # 75% minimum
    assert results.sml_to_snowflake_dropoff <= 25  # 25% maximum

def test_probability_dataset():
    """Test auto-detection remains perfect."""
    results = analyze_dataset("Probability")
    assert results.success_rate == 1.0  # 100%
```

---

## Success Criteria

| Metric | Current | Target | Priority |
|--------|---------|--------|----------|
| Auto-detection success | 100% | 100% | Critical ✓ |
| Simple measure success | 23.4% | 50% | High |
| Complex measure success | 0% | 50% | Medium |
| Overall success | 20.4% | 80% | Critical |
| Time intelligence support | 0% | 100% | High |
| LLM-assisted success | N/A | 90% | Medium |
| Manual override support | 0% | 100% | High |

---

## Risk Mitigation

### Risk: LLM generates invalid SQL
- **Mitigation**: Confidence scoring + human review + sandbox testing
- **Fallback**: Use manual override instead

### Risk: Time intelligence produces wrong results
- **Mitigation**: Compare against Fabric results on test datasets
- **Fallback**: User override available

### Risk: Override validation too strict
- **Mitigation**: Clear error messages + examples
- **Escalation**: Allow advanced users to bypass validation

---

## Cost-Benefit Analysis

| Initiative | Effort | Benefit | ROI |
|-----------|--------|---------|-----|
| Time Intelligence | 2-3 sprints | 39% → 56% success | High |
| Complex Patterns | 4-5 sprints | 56% → 75% success | High |
| LLM Integration | 1-2 sprints | 75% → 90% success | Very High |
| Override Framework | 1-2 sprints | 90% → 100% success | Medium |
| **TOTAL** | **8-12 sprints** | **20% → 100%** | **Very High** |

---

## Questions & Decisions Needed

1. **Priority Sequencing**: Time Intelligence first or LLM support first?
2. **LLM Provider**: Claude (current) or alternative (GPT-4, open-source)?
3. **Confidence Threshold**: When to auto-apply LLM results vs require review?
4. **Override UX**: File upload vs API vs UI editor vs all three?
5. **Timeline**: Complete in 1 quarter? 2 quarters? Track-by-track?

---

## Next Steps

1. Review this plan with stakeholders
2. Prioritize by business impact
3. Assign sprint capacity
4. Implement Priority 1 (Time Intelligence) first
5. Measure success against KPIs
6. Iterate based on real-world usage
