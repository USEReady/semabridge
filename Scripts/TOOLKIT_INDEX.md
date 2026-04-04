# Semabridge CPQ Integration Remediation Toolkit

## Overview

This comprehensive toolkit diagnoses and remedies Salesforce CPQ → Databricks semantic layer integration failures caused by missing foreign key columns in data extraction pipelines.

**Problem Statement:**
When ELT pipelines fail to extract all required Salesforce foreign key columns (SBQQ__Opportunity2__c, Customer_Project__c, etc.), the Semabridge semantic compiler cannot establish relationships between fact and dimension tables, causing all downstream measure views to fail compilation.

**Solution Architecture:**
Three-phase systematic remediation addressing:
1. **Phase 1: ELT Pipeline** — Fix data extraction queries
2. **Phase 2: Databricks** — Create missing dimension tables  
3. **Phase 3: YAML** — Calibrate relationship definitions

---

## Toolkit Files

### 1. **quick_diagnostic.py** ⭐ START HERE
**Purpose:** One-command diagnostic summary of all three phases

```bash
python Scripts/quick_diagnostic.py
```

**Output:**
- ✓/✗ Status of each phase
- Key issues identified
- Specific next steps with commands
- Timeline estimates

**Best for:** Getting immediate visibility into remediation status

---

### 2. **sfdc_cpq_remediation.py**
**Purpose:** Core remediation toolkit with field registry and code generation

```bash
# Display all required Salesforce CPQ fields
python Scripts/sfdc_cpq_remediation.py field-registry

# Generate SOQL and SQL extraction templates
python Scripts/sfdc_cpq_remediation.py generate-elt

# Generate Databricks DDL for missing tables
python Scripts/sfdc_cpq_remediation.py generate-ddl

# Validate and generate corrected YAML
python Scripts/sfdc_cpq_remediation.py validate-yaml
```

**Key Features:**
- Authoritative registry of SBQQ__Quote__c fields
- Standard vs. custom field differentiation
- ELT query generation for CData Sync, Airbyte, UiPath, etc.
- DDL generation for all required dimension tables
- YAML relationship validator and generator

---

### 3. **databricks_auditor.py**
**Purpose:** Real-time Databricks schema auditing with Spark integration

```bash
# Check which SFDC tables exist
python Scripts/databricks_auditor.py audit-tables

# Analyze FK column population rates
python Scripts/databricks_auditor.py audit-fk

# Generate comprehensive audit report
python Scripts/databricks_auditor.py generate-report
```

**Key Features:**
- Spark session integration for live queries
- Table existence checks
- FK column population percentage analysis (shows null rates)
- Markdown report generation
- Graceful fallback for non-Spark environments

**Output Files:**
- `databricks_audit_report.md` — Full audit with tables and metrics

---

### 4. **semabridge_remediation_coordinator.py**
**Purpose:** High-level phase planning and orchestration

```bash
# Display overview of all 3 phases
python Scripts/semabridge_remediation_coordinator.py plan-overview

# Detailed steps for specific phase
python Scripts/semabridge_remediation_coordinator.py plan-phase --phase 1
python Scripts/semabridge_remediation_coordinator.py plan-phase --phase 2
python Scripts/semabridge_remediation_coordinator.py plan-phase --phase 3

# Validate semabridge.yaml
python Scripts/semabridge_remediation_coordinator.py validate-config

# Interactive remediation checklist
python Scripts/semabridge_remediation_coordinator.py checklist
```

**Key Features:**
- Detailed step-by-step remediation plans
- Estimated time for each phase (60-90 min total)
- Validation criteria for success
- Interactive checklist interface
- Integration with semabridge.yaml validation

---

### 5. **REMEDIATION_GUIDE.md**
**Purpose:** Comprehensive reference documentation

- Detailed Phase 1/2/3 procedures
- Tool reference
- Troubleshooting guide
- Data temporal constraints (field history retention)
- Success criteria
- Advanced manual audit SQL

---

## Quick Start (5 Minutes)

### Step 1: Run Diagnostic
```bash
python Scripts/quick_diagnostic.py
```
This identifies which phases need work.

### Step 2: Review Your Status
The diagnostic shows:
- Phase 1 complete? (ELT extraction includes all FK columns)
- Phase 2 complete? (Databricks has dimension tables)
- Phase 3 complete? (YAML relationships defined)

### Step 3: Follow Specific Phase Steps
Each phase has detailed instructions below.

---

## Phase-by-Phase Remediation

### Phase 1: ELT Pipeline Schema Synchronization (20-30 min)

**Goal:** Update Salesforce data extraction to include ALL required foreign keys

```bash
# 1. See what columns to add
python Scripts/sfdc_cpq_remediation.py field-registry

# 2. Generate extraction query templates
python Scripts/sfdc_cpq_remediation.py generate-elt

# 3. Update your extraction tool (CData Sync, UiPath, etc.)
#    Add these columns to SELECT statement:
#    - SBQQ__Opportunity2__c
#    - SBQQ__Account__c
#    - Customer_Project__c
#    - Intake_Form__c
#    - Building_Code__c

# 4. Run full historical load in your ELT tool
```

**Output:** Extraction query now includes all FK columns

**Success Criteria:**
- ✅ All FK columns in extraction query
- ✅ New columns present in Databricks staging tables
- ✅ No NULL values in system FK columns (except custom fields in old records)

---

### Phase 2: Databricks Lakehouse Materialization (20-30 min)

**Goal:** Create missing dimension tables in Databricks

```bash
# 1. Audit current state
python Scripts/databricks_auditor.py audit-tables
python Scripts/databricks_auditor.py audit-fk

# 2. Generate DDL
python Scripts/sfdc_cpq_remediation.py generate-ddl
# Creates: databricks_dimension_tables.sql

# 3. Execute in Databricks SQL Editor
#    - Copy entire databricks_dimension_tables.sql
#    - Paste into Databricks SQL Editor
#    - Run all statements
#    - This creates:
#      - semabridge.public.REP_SFDC_OPPORTUNITY_C
#      - semabridge.public.REP_SFDC_ACCOUNT_C
#      - semabridge.public.REP_SFDC_CUSTOMER_PROJECT_C
#      - semabridge.public.REP_SFDC_INTAKE_FORM_C
#      - semabridge.public.REP_SFDC_BUILDING_CODE_C

# 4. Load data into dimension tables
#    (Extract from Salesforce separately or use Databricks connector)

# 5. Validate FK population
python Scripts/databricks_auditor.py audit-fk
# All FK columns should show >0% population
```

**Output Files:**
- `databricks_dimension_tables.sql` — Table creation scripts
- `databricks_audit_report.md` — Audit results

**Success Criteria:**
- ✅ All 5-7 dimension tables created
- ✅ FK columns in Quote table show >80% population
- ✅ Databricks audit shows no missing tables

---

### Phase 3: Semantic Layer YAML Calibration (15-20 min)

**Goal:** Update Semabridge relationship definitions to match physical schema

```bash
# 1. Validate current YAML
python Scripts/sfdc_cpq_remediation.py validate-yaml

# 2. Review "Generated Relationship YAML" section in output
# 3. Update Config/semabridge.yaml
#    - Open Config/semabridge.yaml
#    - Find 'relationships:' section
#    - Replace with generated YAML block
#    - Save file

# 4. Restart Semabridge API
#    - Kill current process (Ctrl+C)
#    - Run: uv run uvicorn semabridge.api.main:app --host 127.0.0.1 --port 8001
#    - Monitor logs for success messages

# 5. Test measure views
#    - Check logs: should see "Relationships: X compiled"
#    - Should NOT see "Skipping join key" warnings
#    - Should NOT see "measure view failed" errors
```

**Success Criteria:**
- ✅ YAML validates without errors
- ✅ Semabridge API starts without errors
- ✅ Zero "Skipping join key" warnings in logs
- ✅ All measure views compile successfully

---

## Command Reference

### Entry Points (Recommended Order)

1. **First-time setup:**
   ```bash
   python Scripts/quick_diagnostic.py
   ```

2. **Plan all remediation:**
   ```bash
   python Scripts/semabridge_remediation_coordinator.py plan-overview
   ```

3. **Phase-specific guidance:**
   ```bash
   python Scripts/semabridge_remediation_coordinator.py plan-phase --phase 1
   python Scripts/semabridge_remediation_coordinator.py plan-phase --phase 2
   python Scripts/semabridge_remediation_coordinator.py plan-phase --phase 3
   ```

4. **Interactive checklist:**
   ```bash
   python Scripts/semabridge_remediation_coordinator.py checklist
   ```

### Code Generation

```bash
# Review Salesforce field definitions
python Scripts/sfdc_cpq_remediation.py field-registry

# Generate ELT extraction query templates
python Scripts/sfdc_cpq_remediation.py generate-elt

# Generate Databricks DDL
python Scripts/sfdc_cpq_remediation.py generate-ddl

# Validate and generate YAML
python Scripts/sfdc_cpq_remediation.py validate-yaml
```

### Auditing & Validation

```bash
# Databricks schema audit
python Scripts/databricks_auditor.py audit-tables
python Scripts/databricks_auditor.py audit-fk
python Scripts/databricks_auditor.py generate-report

# Configuration validation
python Scripts/semabridge_remediation_coordinator.py validate-config
```

---

## Common Scenarios

### "I don't know where to start"
```bash
python Scripts/quick_diagnostic.py
```
This identifies exactly which phases need work and provides next steps.

### "I need to update my ELT extraction"
```bash
python Scripts/sfdc_cpq_remediation.py generate-elt
```
Shows exact columns to add to SOQL/SQL queries.

### "Databricks tables are missing"
```bash
python Scripts/sfdc_cpq_remediation.py generate-ddl
```
Generates CREATE TABLE statements. Paste in Databricks SQL Editor.

### "Measure views are failing"
```bash
python Scripts/sfdc_cpq_remediation.py validate-yaml
python Scripts/semabridge_remediation_coordinator.py plan-phase --phase 3
```
Generate corrected YAML and update semabridge.yaml.

### "I need a manual audit without Python"
See **REMEDIATION_GUIDE.md** → Advanced: Manual SQL Audit section

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│          quick_diagnostic.py                             │
│  (One-command status check → determines next steps)      │
└────────────┬──────────────────────────────────────────────┘
             │
             ├─→ Phase 1 Issues?
             │   └─→ sfdc_cpq_remediation.py generate-elt
             │       (Generate extraction query templates)
             │
             ├─→ Phase 2 Issues?
             │   ├─→ databricks_auditor.py audit-*
             │   │   (Identify missing tables)
             │   └─→ sfdc_cpq_remediation.py generate-ddl
             │       (Generate DDL for tables)
             │
             └─→ Phase 3 Issues?
                 ├─→ sfdc_cpq_remediation.py validate-yaml
                 │   (Validate and generate YAML)
                 └─→ semabridge_remediation_coordinator.py plan-phase
                     (Step-by-step YAML update guidance)
```

---

## Integration Points

**Semabridge Codebase:**
- `semabridge.connectors.databricks_publisher` — Validates relationships
- `semabridge.api.main` — Exposes /api/sync for validation
- `Config/semabridge.yaml` — Relationship definitions
- `Config/behavior.yaml` — Source table mappings

**Tools can be called programmatically:**
```python
from Scripts.sfdc_cpq_remediation import SFDCCPQFieldRegistry, YAMLRelationshipValidator

# Get field definitions
registry = SFDCCPQFieldRegistry()
fks = registry.get_all_foreign_keys()

# Validate YAML
validator = YAMLRelationshipValidator("Config/semabridge.yaml")
issues = validator.validate_relationships()
```

---

## Support Resources

1. **This Toolkit Overview:** You're reading it
2. **Detailed Guide:** `Scripts/REMEDIATION_GUIDE.md`
3. **Troubleshooting:** See REMEDIATION_GUIDE.md section "Common Issues & Troubleshooting"
4. **Salesforce CPQ Docs:** help.salesforce.com/s/search/contentSearch?query=CPQ
5. **Semabridge Docs:** Check Config/semabridge.yaml schema documentation

---

## Success Criteria

When all three phases are complete:

✅ **Phase 1:** All FK columns in extraction query and present in Databricks
✅ **Phase 2:** All dimension tables created and >80% FK population
✅ **Phase 3:** YAML validated, Semabridge restarts without errors

**Final Validation:**
```bash
# Should show all phases complete
python Scripts/quick_diagnostic.py

# Should show zero issues
python Scripts/sfdc_cpq_remediation.py validate-yaml

# Should show zero warnings
python Scripts/databricks_auditor.py generate-report
```

---

## Next Steps

1. **Run diagnostic:** `python Scripts/quick_diagnostic.py`
2. **Follow phase recommendations from output**
3. **Reference REMEDIATION_GUIDE.md for detailed procedures**
4. **Use checklist for task tracking:** `python Scripts/semabridge_remediation_coordinator.py checklist`

**Estimated Total Time:** 60-90 minutes for all three phases

Good luck! 🚀
