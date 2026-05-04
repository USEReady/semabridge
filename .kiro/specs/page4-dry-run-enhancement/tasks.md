# Implementation Plan: Page 4 Dry Run Enhancement

## Overview

Implement the dry-run → review → edit → deploy user journey for Step 4 of the project creation wizard. The work spans a small backend extension to `mappings_controller.py`, two new React components (`DryRunMappingTable` and `FieldMappingEditor`), and wiring those components into the existing `CreateProjectPage.jsx` Step 4 render block. Backend tests live in `Tests/test_dry_run.py`; frontend tests live alongside the components in `frontend/src/components/__tests__/`.

---

## Tasks

- [x] 1. Extend `UpdateMappingRequest` in `mappings_controller.py`
  - Add `target_data_type: Optional[str] = None` field to the `UpdateMappingRequest` Pydantic model in `src/semabridge/api/controllers/mappings_controller.py`
  - Pass `target_data_type` through to `service.update_mapping_compat()` in the `update_mapping` route handler
  - Ensure the route handler forwards the value so the service layer can persist it; leave the service signature unchanged if it already accepts `**kwargs`
  - _Requirements: 7.1, 7.2, 7.3_

  - [ ]* 1.1 Write unit tests for `UpdateMappingRequest` extension
    - Create `Tests/test_dry_run.py` (or extend if it exists)
    - Test that a PUT request body with `target_data_type` deserialises correctly into `UpdateMappingRequest`
    - Test that a PUT request body without `target_data_type` defaults to `None`
    - Test that the `update_mapping` route handler passes `target_data_type` to the service
    - _Requirements: 7.1, 7.2, 7.3_

- [x] 2. Backend: property-based and unit tests for dry-run filtering and summary
  - Create or extend `Tests/test_dry_run.py` with the tests below
  - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [ ]* 2.1 Write property test: field-only invariant
    - **Property 1: Field-only invariant** — for any list of entity_mappings with mixed `entity_kind` values, the filtered result contains only `field`, `column`, or `measure` kinds
    - Use `hypothesis` with `st.lists(st.fixed_dictionaries({...}))` to generate mixed-kind inputs
    - Assert no `entity_kind == "table"` entry appears in the output of `dry_run_mapping`
    - **Validates: Requirements 1.1**

  - [ ]* 2.2 Write property test: summary consistency
    - **Property 2: Summary consistency** — for any filtered `entity_mappings` list, `summary.total_fields == len(entity_mappings)` and `auto_mapped + unmapped + collisions <= total_fields`
    - Use `hypothesis` to generate lists of field-kind mappings with varying statuses
    - Assert both invariants hold on the summary dict returned by `dry_run_mapping`
    - **Validates: Requirements 1.3, 1.4**

  - [ ]* 2.3 Write unit tests for `add_collision_handling`
    - Test that duplicate `target_name` values produce `mapping_status = "collision"` entries
    - Test that `suggested_target_name` is set to `"{target_name}_{hash4}"` for colliding entries
    - Test that non-colliding entries are returned unchanged
    - _Requirements: 1.1_

- [x] 3. Checkpoint — backend tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Create `DryRunMappingTable` component
  - Create `frontend/src/components/DryRunMappingTable.jsx`
  - Accept props: `mappings`, `onEdit`, `onDeploy`, `isDeploying`, `summary`
  - Render one table row per `NormalizedRow` in `mappings`, showing: source name, source type, target name, target type, `StatusBadge` for mapping status, and an Edit/Map action button
  - Render a summary bar: "N fields — X auto, Y unmapped, Z collisions" using `summary` prop
  - Render filter tabs (All / Auto / Manual / Unmapped / Collision) using the `MAPPING_FILTERS` constant imported from `CreateProjectPage.jsx` or duplicated locally; clicking a tab filters the visible rows
  - Render the Deploy button: disabled when any row satisfies `isBlockingRow` (status === "collision"), enabled otherwise; pass `isDeploying` to show a loading state
  - Render an empty-state message ("No fields found for the selected models") when `mappings` is empty; Deploy button disabled in this state
  - Reuse `StatusBadge` from `frontend/src/components/common/StatusBadge.jsx` and `SmartSearchBar` from `frontend/src/components/common/SmartSearchBar.jsx`
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8_

  - [ ]* 4.1 Write unit tests for `DryRunMappingTable`
    - Create `frontend/src/components/__tests__/DryRunMappingTable.test.jsx`
    - Test that the component renders exactly one row per entry in the `mappings` prop
    - Test that selecting the "Auto" filter tab shows only rows with `status === "auto"`
    - Test that the Deploy button is disabled when at least one row has `status === "collision"`
    - Test that the Deploy button is enabled when no row has `status === "collision"`
    - Test that the empty-state message renders when `mappings` is an empty array
    - _Requirements: 2.2, 2.5, 2.6, 2.7, 2.8_

  - [ ]* 4.2 Write property test: filter tab correctness
    - **Property 9: Filter tab correctness** — for any array of `NormalizedRow` objects and any selected filter tab value, the rows displayed are exactly those whose `status` matches the selected filter (or all rows when "All" is selected)
    - Use `fast-check` to generate arrays of rows with random status values and random filter selections
    - Assert the rendered row count equals the count of matching rows in the input
    - **Validates: Requirements 2.5**

  - [ ]* 4.3 Write property test: Deploy button state invariant
    - **Property 10: Deploy button state invariant** — for any `mappings` array, the Deploy button is enabled if and only if no row satisfies `isBlockingRow(row) = true`
    - Use `fast-check` to generate arrays with varying collision counts
    - Assert button disabled state matches `mappings.some(isBlockingRow)`
    - **Validates: Requirements 2.6, 2.7**

- [x] 5. Create `FieldMappingEditor` component
  - Create `frontend/src/components/FieldMappingEditor.jsx`
  - Accept props: `row`, `onSave`, `onClose`, `targetPlatform`, `isSaving`
  - Wrap content in the existing `Modal` component from `frontend/src/components/common/Modal.jsx`
  - Display `source_name`, `source_table`, and `source_type` as read-only fields
  - Provide a controlled `<input>` for `target_name` and a controlled `<select>` or `<input>` for `target_data_type`
  - On every `target_name` change, call the existing `validateTargetName(value, targetPlatform, sourceType, targetType)` and display the inline error message and suggestion when `isValid === false`
  - On form submit: if validation fails, keep the modal open and show the error; do not call `onSave`
  - On form submit with valid `target_name`: call `onSave(row.id, { target_name, target_data_type })`
  - On close/cancel: call `onClose()` without modifying any row
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

  - [ ]* 5.1 Write unit tests for `FieldMappingEditor`
    - Create `frontend/src/components/__tests__/FieldMappingEditor.test.jsx`
    - Test that `onSave` is called with `{ target_name, target_data_type }` when a valid name is submitted
    - Test that `onSave` is NOT called and an error message is shown when a reserved keyword is submitted
    - Test that `onClose` is called when the cancel button is clicked
    - Test that source fields are rendered as read-only
    - _Requirements: 3.2, 3.3, 3.5, 3.6, 3.7_

  - [ ]* 5.2 Write property test: validation gate
    - **Property 6: Validation gate** — for any `target_name` string for which `validateTargetName(...).isValid === false`, submitting the form does not call `onSave`
    - Use `fast-check` to generate strings that fail validation (empty strings, reserved keywords, strings with unsupported characters)
    - Assert `onSave` mock is never called for invalid inputs
    - **Validates: Requirements 3.5, 5.1**

- [x] 6. Wire up Step 4 in `CreateProjectPage.jsx`
  - Open `frontend/src/pages/CreateProjectPage.jsx` and locate the Step 4 render block
  - Add new state variables near the existing mapping state declarations:
    ```javascript
    const [dryRunData, setDryRunData] = useState(null);
    const [editingRow, setEditingRow] = useState(null);
    const [isSavingEdit, setIsSavingEdit] = useState(false);
    const [isDeploying, setIsDeploying] = useState(false);
    const [deployError, setDeployError] = useState('');
    ```
  - Implement `handleDryRun()`: set `mappingDryRunStatus` to `"loading"`, call the dry-run API, call `normalizeRows(response)`, store result in `detectedMappings` and raw response in `dryRunData`, set status to `"success"` on success or `"error"` with `mappingError` on failure; preserve existing `detectedMappings` on failure
  - Implement `handleFieldEdit(rowId, updates)`: validate with `validateTargetName`, call `PUT /api/projects/preview/mappings/{rowId}` with `{ target_name, target_data_type, status: "manual" }`, update the matching row in `detectedMappings` on success, set `editingRow` to `null`; on API failure set `mappingError` without mutating the row
  - Implement `handleDeploy()`: guard against blocking rows (set `deployError` and return early), call `POST /api/projects/preview/deploy` with the full `detectedMappings` payload, advance `step` to `5` and set `createdProject.id` on success, set `deployError` on failure; always reset `isDeploying` to `false`
  - In the Step 4 JSX: render `<DryRunMappingTable>` when `mappingDryRunStatus === "success"` and `detectedMappings.length > 0`; render `<FieldMappingEditor>` when `editingRow` is non-null; wire the Dry Run button to `handleDryRun`; display `mappingError` and `deployError` inline
  - Import `DryRunMappingTable` and `FieldMappingEditor` at the top of the file
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7_

  - [ ]* 6.1 Write unit tests for `handleFieldEdit` state update
    - Create or extend `frontend/src/pages/__tests__/CreateProjectPage.test.jsx`
    - Test that a successful edit updates the matching row's `target_field`, `target_type`, `status`, and `isDirty` fields
    - Test that all other rows in `detectedMappings` are preserved unchanged
    - Test that `editingRow` is set to `null` after a successful edit
    - Test that a failed API call does not mutate `detectedMappings`
    - _Requirements: 5.2, 5.3, 5.4, 5.5_

  - [ ]* 6.2 Write property test: edit idempotency
    - **Property 3: Edit idempotency** — calling `handleFieldEdit(rowId, updates)` twice with the same updates produces the same final `detectedMappings` state as calling it once
    - Use `fast-check` to generate row arrays and update objects
    - Assert the resulting array is identical after one vs. two applications of the same update
    - **Validates: Requirements 5.2**

  - [ ]* 6.3 Write property test: collision guard
    - **Property 4: Collision guard** — for any `detectedMappings` array containing at least one row where `isBlockingRow(row) === true`, `handleDeploy()` does not call `api.deployMappings()`
    - Use `fast-check` to generate arrays that always include at least one collision row
    - Assert the deploy API mock is never called
    - **Validates: Requirements 6.1, 6.2**

  - [ ]* 6.4 Write property test: deploy payload completeness
    - **Property 8: Deploy payload completeness** — for any `detectedMappings` array of length n with no blocking rows, the payload passed to `api.deployMappings()` contains exactly n entries
    - Use `fast-check` to generate non-empty arrays of non-collision rows
    - Assert `deployMappings` mock receives a payload of the same length as the input array
    - **Validates: Requirements 6.7**

- [x] 7. Checkpoint — full feature wired and tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ]* 8. Write property test: normalization completeness (frontend)
  - Create or extend `frontend/src/utils/__tests__/normalizeRows.test.js`
  - **Property 7: Normalization completeness** — for any `DryRunResponse`, every row in the output of `normalizeRows(data)` has a non-empty `id`, all `id` values are unique, and no row with `entity_kind === "table"` appears in the output
  - Use `fast-check` to generate `DryRunResponse` objects with mixed `entity_kind` values and duplicate ids
  - Assert all three invariants hold on the output array
  - **Validates: Requirements 8.1, 8.2, 8.3**

- [ ]* 9. Write property test: status monotonicity (frontend)
  - **Property 5: Status monotonicity** — for any row whose status has been set to `"manual"` via `handleFieldEdit`, a subsequent dry-run refresh does not revert that row's status to `"auto"` unless the user explicitly triggers a new dry run
  - Simulate a sequence: dry run → edit row to "manual" → second dry run with same data
  - Assert the edited row retains `status === "manual"` after the second dry run
  - **Validates: Requirements 5.2**

---

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation at natural integration boundaries
- The backend extension (Task 1) is a prerequisite for the frontend edit flow (Task 6) to persist `target_data_type`
- `normalizeRows` and `validateTargetName` are existing utilities in `CreateProjectPage.jsx` — do not rewrite them; import or co-locate as needed
- The `isBlockingRow` predicate (used in Tasks 4, 6, and property tests) should be extracted as a shared helper: `row => row.status === "collision"`
- Property tests use `hypothesis` (Python) and `fast-check` (JavaScript/React)
