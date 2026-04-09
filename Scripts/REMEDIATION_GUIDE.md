# Salesforce CPQ → Databricks Semantic Layer Remediation Toolkit

## Overview

This toolkit provides comprehensive diagnostics and remediation for Semabridge integration failures caused by missing foreign key columns in Salesforce CPQ data extraction pipelines.

**Problem:** When ELT pipelines fail to include all required FK columns (SBQQ__Opportunity2__c, Customer_Project__c, etc.), the Semabridge semantic compiler cannot establish relationships between Quote fact tables and dimension tables, causing measure view compilation to fail.

**Solution:** Three-phase systematic remediation:
1. **ELT Pipeline Schema Synchronization** — Fix data extraction queries
2. **Databricks Lakehouse Materialization** — Create missing dimension tables
3. **Semantic Layer YAML Calibration** — Update relationship definitions

---

## Quick Start

### 1. View Remediation Plan (5 min)
```bash
python Scripts/semabridge_remediation_coordinator.py plan-overview
```
See all 3 phases and key objectives.

### 2. Review Field Registry (5 min)
```bash
python Scripts/sfdc_cpq_remediation.py field-registry
```
Understand which Salesforce fields are required and expected.

### 3. Run Full Diagnostic (10 min)
```bash
# Audit Databricks
python Scripts/databricks_auditor.py audit-tables
python Scripts/databricks_auditor.py audit-fk
python Scripts/databricks_auditor.py generate-report

# Validate YAML
python Scripts/sfdc_cpq_remediation.py validate-yaml
```

---

## Three-Phase Remediation

### Phase 1: ELT Pipeline Schema Synchronization (20-30 min)

**Objective:** Update your Salesforce data extraction queries to include ALL required foreign keys.

#### Step 1.1: Review Current Extraction Configuration
```bash
python Scripts/sfdc_cpq_remediation.py generate-elt
```

This shows:
- **SOQL Pattern** — For direct Salesforce API calls
- **SQL Pattern** — For CData Sync, UiPath, Airbyte, etc.

**Example output:**
```sql
SELECT [Id], 
       [Name], 
       [CreatedDate],
       [LastModifiedDate],
       [SBQQ__Opportunity2__c],      -- ✓ Add this
       [SBQQ__Account__c],           -- ✓ Add this
       [SBQQ__PrimaryQuote__c],
       [SBQQ__OriginalQuote__c],
       [SBQQ__OrderGroupID__c],
       [Customer_Project__c],        -- ✓ Add this (custom)
       [Intake_Form__c],             -- ✓ Add this (custom)
       [Building_Code__c]            -- ✓ Add this (custom)
FROM [SBQQ_Quote__c]
WHERE [LastModifiedDate] >= '{env:start_extraction_date}'
  AND [LastModifiedDate] <= '{env:end_extraction_date}'
```

#### Step 1.2: Update Your ELT Tool
- **CData Sync:** Replace query in data source configuration
- **UiPath:** Update SOQL in Orchestrator activity
- **Airbyte:** Modify source SOQL
- **Custom Scripts:** Update extraction SQL query

**Critical:** Include these specific columns:
```
SBQQ__Opportunity2__c      (primary FK to Opportunity)
SBQQ__Account__c           (FK to Account)
Customer_Project__c        (custom FK)
Intake_Form__c             (custom FK)
Building_Code__c           (custom FK)
```

#### Step 1.3: Run Initial Load
Execute a **full historical reload** of SBQQ__Quote__c in your ELT tool to capture all records with new columns.

**Note on Historical Data:** If your Salesforce org has custom lookup fields added recently, older records may not have values populated. See Data Temporal Constraints below.

---

### Phase 2: Databricks Lakehouse Materialization (20-30 min)

**Objective:** Create missing dimension tables and validate schema in Databricks.

#### Step 2.1: Audit Current State
```bash
python Scripts/databricks_auditor.py audit-tables
python Scripts/databricks_auditor.py audit-fk
python Scripts/databricks_auditor.py generate-report
```

**Expected output:** Missing tables and FK columns flagged as ❌

#### Step 2.2: Generate DDL
```bash
python Scripts/sfdc_cpq_remediation.py generate-ddl
```

**Output:** Creates `databricks_dimension_tables.sql` with statements like:
```sql
CREATE TABLE IF NOT EXISTS semabridge.public.REP_SFDC_OPPORTUNITY_C (
    Id STRING NOT NULL,
    Name STRING,
    AccountId STRING,
    Amount DECIMAL(18,2),
    ...
)
USING DELTA;

CREATE TABLE IF NOT EXISTS semabridge.public.REP_SFDC_CUSTOMER_PROJECT_C (
    Id STRING NOT NULL,
    Name STRING,
    Project_Code__c STRING,
    Deal_Score__c DECIMAL(10,2),
    ...
)
USING DELTA;
```

#### Step 2.3: Execute in Databricks
1. Open [Databricks SQL Editor](https://databricks.com)
2. Copy entire `databricks_dimension_tables.sql`
3. Execute it
4. Verify all 5-7 tables created:
   - `REP_SFDC_OPPORTUNITY_C`
   - `REP_SFDC_ACCOUNT_C`
   - `REP_SFDC_CUSTOMER_PROJECT_C`
   - `REP_SFDC_INTAKE_FORM_C`
   - `REP_SFDC_BUILDING_CODE_C`

#### Step 2.4: Populate Dimension Tables
You'll need to separately extract Opportunity, Account, and custom object data from Salesforce:

```bash
# These should be in your ELT pipeline as well
SELECT * FROM Opportunity WHERE LastModifiedDate >= '{start_date}'
SELECT * FROM Account WHERE LastModifiedDate >= '{start_date}'
SELECT * FROM Customer_Project__c WHERE LastModifiedDate >= '{start_date}'
```

Or load from Salesforce manually if using Databricks connectors.

#### Step 2.5: Validate Foreign Keys
```bash
python Scripts/databricks_auditor.py audit-fk
```

**Expected result:** All FK columns show > 0% population:
```
SBQQ__Opportunity2__c  ✓ 45000/50000  100%
Customer_Project__c    ✓ 40000/50000  80%
Intake_Form__c         ✓ 35000/50000  70%
```

---

### Phase 3: Semantic Layer YAML Calibration (15-20 min)

**Objective:** Update Semabridge relationship definitions to match physical schema.

#### Step 3.1: Validate Current YAML
```bash
python Scripts/sfdc_cpq_remediation.py validate-yaml
```

**Output shows:**
- ❌ Missing relationship errors (if any)
- Generated corrected YAML block

#### Step 3.2: Review Generated YAML
```yaml
relationships:
  REL_QUOTE_TO_OPPORTUNITY:
    joining_dimension: REP_SFDC_OPPORTUNITY_C
    cardinality: many_to_one
    join_keys:
      - dimension_field: Id
        foreign_key: SBQQ__Opportunity2__c
        type: source_key_to_dimension_key
        
  REL_QUOTE_TO_CUSTOMER_PROJECT:
    joining_dimension: REP_SFDC_CUSTOMER_PROJECT_C
    cardinality: many_to_one
    join_keys:
      - dimension_field: Id
        foreign_key: Customer_Project__c
        type: source_key_to_dimension_key
```

#### Step 3.3: Update Config/semabridge.yaml
1. Open `Config/semabridge.yaml`
2. Locate `relationships:` section
3. Copy entire corrected block from Step 3.1
4. Replace existing relationships
5. Save file

#### Step 3.4: Restart Semabridge API
```bash
# Stop current API (Ctrl+C)
# Restart
uv run uvicorn semabridge.api.main:app --host 127.0.0.1 --port 8001
```

**Monitor logs for:**
- `[INFO] Relationships: X compiled successfully` ✓
- ❌ NO "Skipping join key" warnings
- ❌ NO "measure view failed" errors

#### Step 3.5: Test Measure Views
```bash
# Call API to trigger model validation
curl -X POST http://127.0.0.1:8001/api/sync \
  -H "Content-Type: application/json" \
  -d '{"validate_only": true}'
```

**Success indicators:**
- All measure views compile
- Relationships established (not skipped)
- FK columns resolved

---

## Detailed Tool Reference

### 1. sfdc_cpq_remediation.py
General-purpose remediation tool with 4 commands:

```bash
python Scripts/sfdc_cpq_remediation.py field-registry
```
Display Salesforce CPQ field definitions with types and purposes.

```bash
python Scripts/sfdc_cpq_remediation.py generate-elt
```
Generate SOQL and SQL extraction templates.

```bash
python Scripts/sfdc_cpq_remediation.py generate-ddl
```
Generate Databricks table creation scripts (saves to `databricks_dimension_tables.sql`).

```bash
python Scripts/sfdc_cpq_remediation.py validate-yaml [--yaml CONFIG_PATH]
```
Validate and generate corrected relationship YAML.

---

### 2. databricks_auditor.py
Real-time Databricks schema auditing with Spark integration:

```bash
python Scripts/databricks_auditor.py audit-tables
```
Check which SFDC tables exist in Databricks.

```bash
python Scripts/databricks_auditor.py audit-fk
```
Analyze foreign key column population rates (shows % null).

```bash
python Scripts/databricks_auditor.py generate-report
```
Create comprehensive markdown audit report (saves to `databricks_audit_report.md`).

---

### 3. semabridge_remediation_coordinator.py
High-level planning and coordination:

```bash
python Scripts/semabridge_remediation_coordinator.py plan-overview
```
Display all 3 phases with objectives.

```bash
python Scripts/semabridge_remediation_coordinator.py plan-phase --phase 1
```
Detailed steps for Phase N (1, 2, or 3).

```bash
python Scripts/semabridge_remediation_coordinator.py validate-config
```
Check semabridge.yaml for issues.

```bash
python Scripts/semabridge_remediation_coordinator.py checklist
```
Interactive remediation checklist.

---

## Common Issues & Troubleshooting

### Issue: "Column X does not exist in Databricks"
**Cause:** ELT pipeline not extracting the column.
**Fix:** Execute Phase 1 — update extraction query.

### Issue: "measure_view failed — source table may not exist"
**Cause:** Dimension table not materialized.
**Fix:** Execute Phase 2 — create dimension tables with DDL.

### Issue: "Skipping join key for relationship REL_X_Y"
**Cause:** YAML relationship references a column that doesn't exist.
**Fix:** Execute Phase 3 — update YAML with corrected definitions.

### Issue: Historical records missing FK values
**Cause:** Custom fields were added recently; old records weren't populated.
**Solution:** Salesforce retains field history for 24 months (API). Activate Field Audit Trail for unlimited history. Or run extraction from custom field creation date forward.

### Issue: Databricks table empty
**Cause:** Extraction successful but dimension data never loaded.
**Fix:** Separately extract Opportunity, Account, etc. objects from Salesforce and load to Databricks.

---

## Data Temporal Constraints

**Important:** Salesforce field history is retained for:
- **24 months** via standard API
- **10 years** with Field Audit Trail (premium feature)

If custom FK fields (Customer_Project__c, etc.) were recently added:
- **New records** have FK values ✓
- **Old records** have NULL FK values (before field creation)

**Mitigation:**
1. Activate Field Audit Trail in Salesforce for unlimited history
2. Or accept NULL values in older records
3. Or populate FK values retroactively via Salesforce Apex/flows

---

## Advanced: Manual SQL Audit

If Python tools are unavailable, run SQL directly in Databricks:

### Check table existence
```sql
SELECT table_schema, table_name
FROM semabridge.information_schema.tables
WHERE table_schema = 'public'
  AND table_name LIKE 'REP_SFDC_%'
ORDER BY table_name;
```

### Check FK column existence and population
```sql
SELECT
    COUNT(*) as total_rows,
    COUNT(CASE WHEN SBQQ__Opportunity2__c IS NOT NULL THEN 1 END) as opp_populated,
    COUNT(CASE WHEN Customer_Project__c IS NOT NULL THEN 1 END) as proj_populated,
    ROUND(100.0 * COUNT(CASE WHEN SBQQ__Opportunity2__c IS NULL THEN 1 END) / COUNT(*), 2) as opp_null_pct
FROM semabridge.public.REP_SFDC_SBQQ__QUOTE__C;
```

### Check current YAML relationships
```bash
grep -A 10 "relationships:" Config/semabridge.yaml
```

---

## Integration with Semabridge Codebase

These tools integrate with:
- **semabridge.connectors.databricks_publisher** — Validates relationships during compilation
- **semabridge.api.main** — Exposes /api/sync for validation
- **Config/semabridge.yaml** — Relationship definitions
- **Config/behavior.yaml** — Source table mapping

The toolkit provides **validation hooks** that can be called from:
```python
from Scripts.sfdc_cpq_remediation import YAMLRelationshipValidator

validator = YAMLRelationshipValidator("Config/semabridge.yaml")
issues = validator.validate_relationships()
```

---

## Success Criteria

✅ **Phase 1 Complete:**
- All FK columns added to extraction query
- Quote table in Databricks contains these columns
- No NULL values in system FK columns (SBQQ__Opportunity2__c, SBQQ__Account__c)

✅ **Phase 2 Complete:**
- All 5-7 dimension tables created in Databricks
- Tables populated with data
- FK columns in Quote table > 80% populated

✅ **Phase 3 Complete:**
- semabridge.yaml relationships validated
- Semabridge API restarts without errors
- No "Skipping join key" warnings in logs
- Measure views compile successfully
- Test queries return results with proper dimensional context

---

## Next Steps

1. **Start with Phase 1:** `python Scripts/semabridge_remediation_coordinator.py plan-phase --phase 1`
2. **Follow the checklist:** `python Scripts/semabridge_remediation_coordinator.py checklist`
3. **Review progress:** `python Scripts/databricks_auditor.py generate-report`
4. **Validate final state:** `python Scripts/sfdc_cpq_remediation.py validate-yaml`

Good luck! 🚀
