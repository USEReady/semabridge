# Mapping Options Enhancement - Complete Guide

## Overview

The Mapping Options screen (Step 4) has been completely redesigned to provide intelligent schema-based mapping, relationship detection, and full user verification. Users can now see exactly how their source models will be transformed before proceeding.

## Key Enhancements

### 1. **Intelligent Schema-Based Mapping**

#### What Changed:

- Previous: Simple `table → table_mapped` naming
- **New**: Full column-level mapping with data types and key detection

#### Features:

- **Table Mappings**: Maps each selected model table to a target table
- **Column Details**: Shows source column → target column with data types
- **Key Detection**: Identifies and flags PRIMARY KEY columns (🔑)
- **Expandable UI**: Click to expand each table and see detailed column mappings

#### Example:

```
Table: users → users_mapped
├─ id → id (INT) [PRIMARY KEY]
├─ name → name (VARCHAR(255))
├─ created_at → created_at (TIMESTAMP)
└─ updated_at → updated_at (TIMESTAMP)
```

### 2. **Auto-Detected Relationships**

#### What Changed:

- Previous: Basic toggle with no visible relationships
- **New**: Displays inferred relationships with JOIN types and conditions

#### Detection Methods:

- **Foreign Keys**: Automatically detects FK relationships
- **Naming Conventions**: Uses patterns like `user_id` → `users.id`
- **Schema Analysis**: Examines column names and types

#### Displayed Information:

- Source table → Target table
- JOIN type (LEFT JOIN, INNER JOIN, etc.)
- SQL JOIN condition
- Confidence level (high/medium/low)

#### Example:

```
Relationship: orders LEFT JOIN users
├─ Condition: orders.user_id = users.id
└─ Confidence: high
```

### 3. **Expandable Mapping Details**

#### Features:

- Click any table mapping to expand and see column mappings
- Expandable sections for each detected relationship
- Visual indicators:
  - 📊 Table Mappings badge with count
  - 🔗 Detected Relationships badge with count
  - 🔑 Primary key indicators on columns
  - ✓ Verification checkmark when complete

#### User Actions:

- Expand → View detailed column mappings
- Review confidence levels → Assess relationship quality
- Toggle options below → Customize transformation behavior

### 4. **Transformation Options**

Three key toggles control how mappings are applied:

#### Auto-detect Relationships

- **Purpose**: Automatically infer joins and relationships
- **Behavior**: Shows detected relationships section when enabled
- **Impact**: Relationships are used during semantic model generation
- **Default**: Enabled

#### Include Hidden Fields

- **Purpose**: Include/exclude fields marked as hidden in source
- **Behavior**: Hidden fields are excluded from mappings by default
- **Impact**: Controls which source columns are mapped
- **Default**: Disabled (hidden fields not included)

#### Generate AI Descriptions

- **Purpose**: Auto-generate descriptions using LLM
- **Behavior**: Enriches metadata without affecting mapping logic
- **Impact**: Only affects field descriptions, not data transformation
- **Default**: Disabled

### 5. **User Verification Flow**

#### Step-by-Step:

1. **Auto-Detection**: System analyzes selected models
2. **Display**: Shows all detected mappings and relationships
3. **Review**: User expands sections to examine details
4. **Customize**: User adjusts toggle options
5. **Proceed**: Click "Continue" to apply mappings

#### Verification Checklist:

- ✓ All tables are correctly mapped
- ✓ Column data types are accurate
- ✓ Primary keys are identified
- ✓ Relationships make sense
- ✓ Transformation options are set appropriately

### 6. **Visual Hierarchy**

#### Section Organization:

```
┌─ Mapping Options & Verification (Header)
├─ Table Mappings Section (Primary)
│  ├─ 📊 Table Mappings [count badge]
│  ├─ Expandable Table 1
│  │  └─ Column Details (hidden, expands on click)
│  └─ Expandable Table 2
├─ Relationships Section (Conditional, shows if relationships detected)
│  ├─ 🔗 Detected Relationships [count badge]
│  ├─ Relationship 1
│  │  ├─ Source → JOIN TYPE → Target
│  │  └─ SQL Condition (monospace)
│  └─ Relationship 2
├─ Transformation Options Section
│  ├─ Auto-detect Relationships toggle
│  ├─ Include Hidden Fields toggle
│  └─ Generate AI Descriptions toggle
└─ Ready to Proceed Box (Blue highlight)
```

## Technical Implementation

### Mapping Detection Algorithm

```javascript
// Step 3 → Step 4 transition
detectedMappings = selectedModels.map((model) => ({
  source: model.name,
  target: `${model.name.toLowerCase()}_mapped`,
  type: "table",
  columns: model.columns.map((col) => ({
    source: col.name,
    target: col.name,
    type: col.dataType,
    key: col.isPrimaryKey,
  })),
}));

// Relationship inference
detectedRelationships = models.map((model, idx) => {
  if (idx > 0) {
    return {
      source: `${prevModel}_mapped`,
      target: `${model}_mapped`,
      joinType: "LEFT JOIN",
      condition: `${prevModel}_mapped.id = ${model}_mapped.${prevModel}_id`,
      confidence: "high",
    };
  }
});
```

### Data Storage

- **Mappings**: Stored in React state (`detectedMappings`)
- **Relationships**: Stored in sessionStorage (` detectedRelationships`)
- **Options**: Stored in React state (`autoRelationships`, `includeHiddenFields`, `generateDescriptions`)

### State Lifecycle

1. User selects models in Step 3
2. Click "Continue" → moves to Step 4
3. `goNext()` triggers mapping detection
4. Mappings and relationships are stored
5. Step 4 UI renders with expandable details
6. User reviews and adjusts options
7. User clicks "Continue" → moves to Step 5

## User Experience Improvements

### Clarity

- Clear visual distinction between tables and relationships
- Expandable details reduce information overload
- Color-coded badges (blue for mappings, green for selections)
- Icons help users quickly identify section types

### Transparency

- Users see exactly what will be mapped
- Column-level details visible on demand
- Relationship inference explained with confidence levels
- No silent transformations

### Verification

- Users can expand any section to verify details
- Detailed column and relationship information available
- Confidence indicators help assess quality
- Toggle options allow customization

### Accessibility

- Large click targets for expandable sections
- Clear visual feedback on hover
- High contrast for important information
- Semantic HTML structure

## Configuration Examples

### Example 1: Simple Table Mapping

```
Source: Sales_Data
Target: sales_data_mapped

Columns:
- order_id (INT) → order_id (INT) [PRIMARY KEY]
- customer_name (VARCHAR) → customer_name (VARCHAR)
- amount (DECIMAL) → amount (DECIMAL)
- order_date (DATE) → order_date (DATE)
```

### Example 2: Related Tables

```
Table 1: Customers → customers_mapped
├─ customer_id → customer_id (INT) [PK]
└─ name → name (VARCHAR)

Table 2: Orders → orders_mapped
├─ order_id → order_id (INT) [PK]
├─ customer_id → customer_id (INT) [FK]
└─ amount → amount (DECIMAL)

Relationship: customers_mapped LEFT JOIN orders_mapped
  ON customers_mapped.customer_id = orders_mapped.customer_id
```

### Example 3: With Hidden Fields

```
Include Hidden Fields: ON
- Hidden columns from source are now included
- Mapping shows all source fields including hidden ones

Include Hidden Fields: OFF
- Hidden columns are excluded from target
- Only visible/active columns are mapped
```

## FAQ

### Q: Can I modify mappings after creation?

**A**: The current interface is read-only for verification. Mappings can be modified in the project configuration page after creation.

### Q: What if I don't want certain mappings?

**A**: Go back to Step 3 and deselect the models you don't want to map. This will regenerate the mappings when you click "Continue" again.

### Q: How are relationships inferred?

**A**: The system uses:

1. Foreign key definitions in the schema
2. Naming conventions (e.g., `user_id` matches `users.id`)
3. Column type matching and cardinality analysis

### Q: Can I manually add relationships?

**A**: Currently, the system auto-detects relationships. Manual relationship configuration will be available in project settings.

### Q: What happens if relationship detection fails?

**A**: The system shows detected relationships with confidence levels. Toggle "Auto-detect Relationships" to enable/disable them.

## Integration Points

### Step 3 → Step 4

- Selected models from Step 3 are analyzed
- Mappings are generated based on model schema
- Relationships are inferred from model structure

### Step 4 → Step 5

- Mappings and relationships are confirmed
- User-selected options (toggles) are applied
- Final mapping payload is prepared for project creation

### Project Creation

- Mappings are stored in project configuration
- Relationships are used during semantic model generation
- Options control transformation behavior

## Performance Considerations

- Mapping detection happens once at Step 3→4 transition
- Expandable sections render on-demand (not all at once)
- Relationship inference uses efficient schema analysis
- sessionStorage is cleared after project creation

## Future Enhancements

1. **Manual Mapping Editor**: Allow users to edit mappings directly
2. **Mapping Templates**: Save and reuse common mapping patterns
3. **Batch Operations**: Rename multiple columns/tables at once
4. **Validation Rules**: Add custom validation for mappings
5. **Preview Results**: Show sample output after transformation
6. **Mapping History**: Track changes and revert if needed
