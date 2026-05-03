# Requirements Document

## Introduction

The Page 4 Dry Run Enhancement adds a complete dry-run → review → edit → deploy user journey to the project creation wizard's Mapping Options step (Step 4). Users can trigger a dry run against selected source models, review the resulting field-level mappings in an interactive table, edit individual field target names and data types inline, and then deploy the finalized mappings to advance to Step 5. The backend dry-run endpoint already filters to field-level entities; this feature surfaces those results in the UI and adds the minimal backend extension needed to persist target data type edits.

## Glossary

- **DryRunMappingTable**: New React component that renders field-level mapping results as a sortable, filterable, editable table.
- **FieldMappingEditor**: New React modal component for editing a single field mapping's target name and data type.
- **CreateProjectPage**: Existing React page component hosting the multi-step project creation wizard.
- **NormalizedRow**: Frontend data model produced by `normalizeRows()` representing a single field mapping with status, source info, and target info.
- **DryRunSummary**: Object containing aggregate counts (total_fields, auto_mapped, unmapped, collisions) returned by the dry-run endpoint.
- **MappingStatus**: One of four values — `auto`, `manual`, `unmapped`, or `collision` — indicating how a field mapping was resolved.
- **CollisionRow**: A NormalizedRow whose status is `collision`, indicating a target name conflict that must be resolved before deployment.
- **UpdateMappingRequest**: Pydantic model in `mappings_controller.py` that accepts a field mapping update payload from the frontend.
- **validateTargetName**: Existing frontend utility function that checks a target name string against platform-specific rules (reserved keywords, unsupported characters, type compatibility).
- **normalizeRows**: Existing frontend utility function that transforms a raw DryRunResponse into an array of NormalizedRow objects.
- **add_collision_handling**: Existing backend function that marks duplicate target names as collisions and sets a suggested alternative.
- **SmartSearchBar**: Existing reusable frontend search component.
- **StatusBadge**: Existing reusable frontend badge component for rendering MappingStatus values.
- **Modal**: Existing reusable frontend modal wrapper component.

---

## Requirements

### Requirement 1: Dry Run Endpoint Returns Field-Only Mappings

**User Story:** As a project creator, I want the dry run to return only field-level mappings, so that I see a clean, actionable list of columns and measures without confusing table-level or creation-key artifacts.

#### Acceptance Criteria

1. WHEN the dry-run endpoint processes a mapping request, THE Backend SHALL return only entity_mappings entries whose entity_kind is one of `field`, `column`, or `measure`.
2. WHEN the dry-run endpoint returns a response, THE Backend SHALL include a `summary` object containing `total_fields`, `auto_mapped`, `unmapped`, and `collisions` counts.
3. WHEN the dry-run endpoint returns a response, THE Backend SHALL ensure `summary.total_fields` equals the length of the `entity_mappings` array in that response.
4. WHEN the dry-run endpoint returns a response, THE Backend SHALL ensure `summary.auto_mapped + summary.unmapped + summary.collisions` is less than or equal to `summary.total_fields`.

---

### Requirement 2: DryRunMappingTable Component

**User Story:** As a project creator, I want to see all detected field mappings in a table after running a dry run, so that I can review which fields were auto-mapped, which have collisions, and which are unmapped.

#### Acceptance Criteria

1. WHEN `detectedMappings` is non-empty and `mappingDryRunStatus` is `success`, THE CreateProjectPage SHALL render the DryRunMappingTable component.
2. WHEN the DryRunMappingTable renders, THE DryRunMappingTable SHALL display one row per NormalizedRow in the `mappings` prop.
3. WHEN the DryRunMappingTable renders a row, THE DryRunMappingTable SHALL display the source name, source type, target name, target type, a MappingStatus badge, and an Edit/Map action button for that row.
4. WHEN the DryRunMappingTable renders, THE DryRunMappingTable SHALL display a summary bar showing total field count and per-status counts (auto, unmapped, collisions).
5. WHEN a user selects a filter tab (All, Auto, Manual, Unmapped, Collision), THE DryRunMappingTable SHALL display only the rows whose status matches the selected filter.
6. WHEN the `mappings` prop contains one or more CollisionRows, THE DryRunMappingTable SHALL render the Deploy button in a disabled state.
7. WHEN the `mappings` prop contains no CollisionRows, THE DryRunMappingTable SHALL render the Deploy button in an enabled state.
8. WHEN `detectedMappings` is empty and `mappingDryRunStatus` is `success`, THE DryRunMappingTable SHALL display an empty-state message indicating no fields were found and SHALL render the Deploy button in a disabled state.

---

### Requirement 3: FieldMappingEditor Component

**User Story:** As a project creator, I want to edit a field's target name and data type in a modal, so that I can correct auto-mapped names or resolve collisions before deploying.

#### Acceptance Criteria

1. WHEN a user clicks the Edit/Map button on a DryRunMappingTable row, THE CreateProjectPage SHALL open the FieldMappingEditor modal with that row's data.
2. WHEN the FieldMappingEditor renders, THE FieldMappingEditor SHALL display the source name, source table, and source type as read-only fields.
3. WHEN the FieldMappingEditor renders, THE FieldMappingEditor SHALL provide an editable input for `target_name` and an editable select or input for `target_data_type`.
4. WHEN a user changes the `target_name` input, THE FieldMappingEditor SHALL invoke `validateTargetName` and display an inline error message if the value is invalid.
5. IF the user submits the FieldMappingEditor with an invalid `target_name`, THEN THE FieldMappingEditor SHALL remain open, display the validation error and a suggested alternative, and SHALL NOT call the mapping update API.
6. WHEN a user submits the FieldMappingEditor with a valid `target_name`, THE FieldMappingEditor SHALL call `onSave` with a payload containing exactly `{ target_name, target_data_type }`.
7. WHEN a user clicks the close or cancel control in the FieldMappingEditor, THE FieldMappingEditor SHALL close without modifying any row in `detectedMappings`.

---

### Requirement 4: Dry Run Trigger and State Management in CreateProjectPage

**User Story:** As a project creator, I want to trigger a dry run from Step 4 and see live feedback on its progress, so that I know when results are ready to review.

#### Acceptance Criteria

1. WHEN a user clicks the Dry Run button on Step 4, THE CreateProjectPage SHALL set `mappingDryRunStatus` to `loading` and disable the Dry Run button for the duration of the API call.
2. WHEN the dry-run API call succeeds, THE CreateProjectPage SHALL invoke `normalizeRows` on the response and store the result in `detectedMappings`.
3. WHEN the dry-run API call succeeds, THE CreateProjectPage SHALL set `mappingDryRunStatus` to `success`.
4. IF the dry-run API call returns a non-2xx status or `{ success: false }`, THEN THE CreateProjectPage SHALL set `mappingDryRunStatus` to `error` and set `mappingError` to a non-empty human-readable message.
5. IF the dry-run API call fails, THEN THE CreateProjectPage SHALL preserve the existing `detectedMappings` value without clearing it.
6. WHEN `mappingDryRunStatus` is `error`, THE CreateProjectPage SHALL display the `mappingError` message inline above the Dry Run button.

---

### Requirement 5: Field Edit Flow

**User Story:** As a project creator, I want to save edits to a field mapping and see the row update immediately, so that I can iteratively refine mappings before deploying.

#### Acceptance Criteria

1. WHEN the FieldMappingEditor calls `onSave` with valid updates, THE CreateProjectPage SHALL call `PUT /api/projects/{project_id}/mappings/{id}` with `{ target_name, target_data_type, status: "manual" }`.
2. WHEN the mapping update API call succeeds, THE CreateProjectPage SHALL update the matching row in `detectedMappings` so that `target_field` equals the new `target_name`, `target_type` equals the new `target_data_type`, `status` equals `"manual"`, and `isDirty` equals `true`.
3. WHEN the mapping update API call succeeds, THE CreateProjectPage SHALL close the FieldMappingEditor modal by setting `editingRow` to `null`.
4. IF the mapping update API call fails, THEN THE CreateProjectPage SHALL set `mappingError` to a non-empty message and SHALL NOT update the row in `detectedMappings`.
5. WHEN `handleFieldEdit` updates `detectedMappings`, THE CreateProjectPage SHALL preserve all rows that do not match the edited `rowId` without modification.

---

### Requirement 6: Deploy Flow

**User Story:** As a project creator, I want to deploy the finalized field mappings and advance to Step 5, so that I can complete the project creation wizard after reviewing and editing mappings.

#### Acceptance Criteria

1. WHEN a user clicks the Deploy button, THE CreateProjectPage SHALL check whether any row in `detectedMappings` satisfies `isBlockingRow(row) = true`.
2. IF any row in `detectedMappings` satisfies `isBlockingRow(row) = true`, THEN THE CreateProjectPage SHALL set `deployError` to a non-empty message and SHALL NOT call the deploy API.
3. WHEN no blocking rows exist and the user clicks Deploy, THE CreateProjectPage SHALL set `isDeploying` to `true` and call `POST /api/projects/{project_id}/deploy` with the full `detectedMappings` payload.
4. WHEN the deploy API call succeeds, THE CreateProjectPage SHALL set `createdProject.id` to the returned `project_id` and advance `step` to `5`.
5. IF the deploy API call returns an error or `{ success: false }`, THEN THE CreateProjectPage SHALL set `deployError` to a non-empty human-readable message and SHALL keep `step` at `4`.
6. WHEN the deploy API call completes (success or failure), THE CreateProjectPage SHALL set `isDeploying` to `false`.
7. WHEN `handleDeploy` maps `detectedMappings` to the deploy payload, THE CreateProjectPage SHALL produce exactly one payload entry per row in `detectedMappings`.

---

### Requirement 7: Backend UpdateMappingRequest Extension

**User Story:** As a developer, I want the mapping update endpoint to accept a target data type, so that frontend edits to data type are persisted alongside target name changes.

#### Acceptance Criteria

1. THE UpdateMappingRequest model SHALL accept a `target_data_type` field of type `Optional[str]` with a default value of `None`.
2. WHEN a PUT request to `/api/projects/{project_id}/mappings/{id}` includes a `target_data_type` value, THE Backend SHALL persist that value on the mapping record.
3. WHEN a PUT request to `/api/projects/{project_id}/mappings/{id}` omits `target_data_type`, THE Backend SHALL leave the existing `target_data_type` value on the mapping record unchanged.

---

### Requirement 8: normalizeRows Correctness

**User Story:** As a developer, I want `normalizeRows` to produce a clean, deduplicated array of NormalizedRow objects, so that the mapping table always has a stable, consistent data source.

#### Acceptance Criteria

1. WHEN `normalizeRows` processes a DryRunResponse, THE normalizeRows function SHALL return an array where every NormalizedRow has a non-empty `id` field.
2. WHEN `normalizeRows` processes a DryRunResponse, THE normalizeRows function SHALL return an array where all `id` values are unique.
3. WHEN `normalizeRows` processes a DryRunResponse containing entries with `entity_kind` equal to `"table"`, THE normalizeRows function SHALL exclude those entries from the returned array.
4. WHEN `normalizeRows` processes a DryRunResponse, THE normalizeRows function SHALL assign each NormalizedRow a `status` value that is one of `"auto"`, `"manual"`, `"unmapped"`, or `"collision"`.
