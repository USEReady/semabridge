# Spec: Page 4 Dry Run — Display & Edit Columns/Measures

## Objective

**User Story:**  
As a user on Page 4 (Mapping Options) of the project creation wizard, when I click "Dry Run," I want to:
1. See a **preview of columns and measures** from my selected semantic model
2. **Edit their mappings** (rename target columns, adjust types, etc.)
3. **Deploy the changes** to execute a source-to-target sync with my edits applied

**Current State:** Dry run returns full response with "creation keys" — confusing for users who only want to review and edit columns/measures.

**Success Criteria:**
- ✅ Dry run endpoint returns clean, field-only response (columns, measures with essential metadata)
- ✅ Page 4 displays columns/measures in an editable table or card layout
- ✅ User can edit target names, data types, and mapping status before deployment
- ✅ "Deploy" button executes source-to-target sync using the edited mappings
- ✅ No extra creation artifacts clutter the UI

---

## Tech Stack

**Frontend:**
- React (existing)
- State: Currently using sessionStorage and component state
- API: `api.runProjectDryRun()` via `utils/api.js`

**Backend:**
- FastAPI (Python)
- Route: `POST /api/projects/{project_id}/dry-run`
- Service: `MappingService.auto_map_compat()`
- Database: DuckDB (project snapshots, ORM)

---

## Commands

```bash
# Development
npm run dev              # Frontend dev server
python -m pytest Tests   # Run tests

# Build
npm run build           # Frontend build
python -m pip install -e . # Backend editable install

# Lint/Format
npm run lint --fix      # ESLint
black src/              # Python formatting
```

---

## Project Structure

**Frontend (Page 4 changes):**
```
frontend/src/pages/CreateProjectPage.jsx
  └─ Step 4: Mapping Options (exists)
     ├─ Dry run trigger
     ├─ Column/measure display (TO BE ENHANCED)
     └─ Edit & deploy flow (TO BE ENHANCED)

frontend/src/components/
  └─ (New) DryRunMappingTable.jsx  # Editable table for columns/measures
  └─ (New) FieldMappingEditor.jsx  # Individual field editor (optional)
```

**Backend (Endpoint enhancements):**
```
src/semabridge/api/controllers/mappings_controller.py
  └─ @router.post("/api/projects/{project_id}/dry-run")
     └─ Return ONLY columns/measures (entity_kind="field"|"column"|"measure")

src/semabridge/api/services/mappings_service.py
  └─ auto_map_compat()  # Existing method, used by dry run
```

---

## Dry Run Response Format

**Current (Full structure):**
```json
{
  "success": true,
  "entity_mappings": [...many creation keys...],
  "mappings": [...creation artifacts...],
  "summary": { "total_fields": X, "auto_mapped": Y, ... }
}
```

**Target (Field-only, clean):**
```json
{
  "success": true,
  "entity_mappings": [
    {
      "id": "field_001",
      "entity_kind": "field",
      "source_name": "CustomerID",
      "source_data_type": "INT",
      "source_table": "Customers",
      "target_name": "customer_id",
      "target_data_type": "INT",
      "mapping_status": "auto",
      "is_key": false,
      "is_hidden": false
    }
  ],
  "summary": {
    "total_fields": 42,
    "auto_mapped": 35,
    "unmapped": 5,
    "collisions": 2
  }
}
```

---

## Page 4 UI Changes

### Before Dry Run:
- [Checkbox] Auto-detect relationships
- [Checkbox] Generate descriptions
- [Button] DRY RUN | [Button] NEXT

### After Dry Run Success:
Display a **column/measure table**:

```
┌────────────────────────────────────────────────────┐
│ Dry Run Results: 42 columns found                  │
├──────────┬──────────┬───────────┬──────────┬────────┤
│ Source   │ Type     │ Target    │ Status   │ Action │
├──────────┼──────────┼───────────┼──────────┼────────┤
│ ID       │ INT      │ id        │ auto ✓   │ [Edit] │
│ Name     │ STRING   │ name      │ auto ✓   │ [Edit] │
│ (unmapped) │ ...    │ [empty]   │ unmapped │ [Map]  │
└──────────┴──────────┴───────────┴──────────┴────────┘

[Edit Column] modal available per row
[Deploy] button becomes active
```

---

## Code Style

### Field Mapping Object (Frontend):

```javascript
const fieldMapping = {
  id: "field_001",
  entity_kind: "field",
  source_name: "CustomerID",
  source_table: "Customers",
  source_data_type: "INT",
  target_name: "customer_id",        // User editable
  target_data_type: "INT",            // User editable
  mapping_status: "auto",             // "auto" | "manual" | "unmapped"
  is_hidden: false,                   // Optional
  is_key: false,                      // Optional
};
```

### Update Mapping (useCallback pattern):

```javascript
const handleFieldEdit = useCallback(async (fieldId, updates) => {
  const projectId = createdProject?.id || "preview";
  const updatedField = {
    ...fieldMapping,
    ...updates,
    mapping_status: "manual", // Mark as manual when edited
  };
  
  try {
    const result = await api.updateMapping(projectId, fieldId, {
      target_name: updatedField.target_name,
      target_data_type: updatedField.target_data_type,
      status: "manual",
    });
    // Refresh mappings table
    setDetectedMappings(prev => 
      prev.map(m => m.id === fieldId ? updatedField : m)
    );
  } catch (error) {
    setMappingError(error.message);
  }
}, [createdProject]);
```

---

## Testing Strategy

### Backend Tests:
- **Unit:** Test `dry_run_mapping()` endpoint filters entity_mappings correctly
- **Integration:** Test end-to-end: select models → dry run → verify response structure

```python
# tests/test_dry_run.py
def test_dry_run_returns_fields_only():
    """Ensure dry run endpoint filters to entity_kind='field' only."""
    response = await client.post(
        "/api/projects/preview/dry-run",
        json={
            "source_config": {"type": "fabric", "workspace_id": "ws-1"},
            "target_config": {"type": "snowflake"},
            "selected_sources": ["Model1"]
        }
    )
    assert response["success"] == True
    for mapping in response["entity_mappings"]:
        assert mapping["entity_kind"] in ["field", "column", "measure"]
```

### Frontend Tests:
- **Unit:** Test filtering/editing logic in Page 4 component
- **Integration:** Test dry run button → response display → edit flow

```javascript
// tests/CreateProjectPage.test.jsx
it("should display columns in editable table after dry run", async () => {
  const { getByText, getByRole } = render(<CreateProjectPage />);
  await userEvent.click(getByText("DRY RUN"));
  await waitFor(() => expect(getByText("42 columns found")).toBeInTheDocument());
  expect(getByRole("table")).toBeInTheDocument();
});
```

---

## Boundaries

- **Always do:**
  - Filter to field-level mappings only in dry run response
  - Validate field edits before sending to backend
  - Track which mappings are manually edited vs. auto-detected
  - Test dry run response filtering

- **Ask first:**
  - Change the `/api/projects/{project_id}/dry-run` response format (may break other clients)
  - Add new fields to entity_mapping objects
  - Modify the deploy flow behavior

- **Never do:**
  - Return creation keys or deployment artifacts in dry run
  - Allow bulk deployment without review
  - Delete unmapped fields silently
  - Skip validation on user-edited fields

---

## Open Questions

1. **Unmapped fields:** Should users be required to map all unmapped fields before deploy, or is partial mapping allowed?
   - Current assumption: Partial mapping allowed (user can deploy with unmapped fields)
   - *Confirm?*

2. **Data type validation:** Should the system validate target data type compatibility (INT → BIGINT OK, INT → STRING warning)?
   - Current assumption: No validation, allow any mapping
   - *Confirm?*

3. **Relationships:** On Page 4 dry run, should relationships also be shown/editable, or just columns/measures?
   - Current assumption: Just columns/measures (relationships are separate)
   - *Confirm?*

4. **Auto-detect relationships toggle:** Should this affect the dry run results?
   - Current assumption: Yes, if enabled, relationships are auto-detected and shown separately
   - *Confirm?*

---

## Implementation Order

1. **Backend (Phase 1):** Enhance `/dry-run` endpoint to return clean field-only response
2. **Frontend (Phase 2):** Add editable column/measure table to Page 4
3. **Integration (Phase 3):** Wire up edit → update → deploy flow
4. **Testing (Phase 4):** Unit & integration tests
5. **Review & Refinement (Phase 5):** Code review, UX refinement

---

## Success Checklist

- [ ] Dry run response contains only field-level mappings
- [ ] Page 4 displays columns/measures in an editable table
- [ ] Users can edit target names and data types
- [ ] Deploy button executes sync with user's edited mappings
- [ ] Tests pass (unit + integration)
- [ ] Code review approved
- [ ] No breaking changes to existing endpoints

