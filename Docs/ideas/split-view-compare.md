# Semantic Snapshot Diff View

## Problem Statement
How might we clearly visualize the structural and semantic changes between two database snapshots side-by-side, so data engineers can instantly audit changes without being overwhelmed by noise?

## Recommended Direction
**The "Focus Mode" Triple-Toggle.** 
We will build a high-density audit log tailored for data engineers featuring three distinct, toggleable views to handle varying levels of schema complexity:
1. **Unified View:** A combined list of all changes (GitHub style).
2. **Split View:** A side-by-side (A/B) structural comparison.
3. **Signal-to-Noise (Semantic) View:** Hides all unchanged elements and only shows rows/columns that experienced modifications, additions, or removals.

This directly addresses the need for an audit log while providing the flexibility to handle dense, massive tables.

## Key Assumptions to Validate
- [ ] Data engineers will prefer a custom UI toggle over a pure text SQL DDL diff.
- [ ] The backend schema comparison logic can accurately identify and classify additions, removals, and modifications at the column level.
- [ ] We can render dense tables in Split View without severe performance degradation in the browser.

## MVP Scope
- Add a sticky header/toolbar to `VersionControlPage.jsx` with three toggle buttons (Unified, Split, Semantic).
- Implement the "Split View" side-by-side rendering logic for tabular schema data.
- Ensure the state toggles seamlessly filter and re-flow the UI components based on the selected mode.

## Not Doing (and Why)
- **Code-as-Truth DDL diffs:** We are focusing on structured UI visualization first to make it accessible to broader engineering personas.
- **Lineage/Impact Analysis:** Out of scope for this MVP. We will just show the structural changes (the "what"), not the downstream impact (the "so what").
- **Time-Travel Slider:** Too complex to build right now and unnecessary for a straightforward audit log requirement.
