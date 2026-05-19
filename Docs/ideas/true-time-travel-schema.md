# True Time Travel Schema Diffing

## Problem Statement
How might we dynamically retrieve and compare full schema snapshots directly from PostgreSQL—replacing monolithic JSON state blobs—so our diff view always has complete structural context while remaining highly performant?

## Recommended Direction: True Time Travel + Lazy Loading
We will transition the backend architecture from storing and diffing opaque JSON state blobs to leveraging the PostgreSQL ORM with dedicated `Schema_History` (or similarly modeled) relational tables. The backend will use optimized SQL `JOIN` or `EXCEPT` queries to calculate drift natively within the database, serving a lightweight summary to the frontend. 

To maintain a snappy UI (and address API response constraints), the frontend will employ a **Lazy Load** mechanism: it will initially receive the high-level structural diff (e.g., "Model Added", "Model Removed") and only fetch the computationally heavier column-level relational diffs via a secondary API call when the user explicitly expands a model's row in the Diff View.

## Key Assumptions to Validate
- [ ] **ORM Performance:** We assume that writing full schema states to relational Postgres tables during synchronization won't bottleneck the deployment process. *Test: Run a benchmark storing a 1,000-table schema via SQLAlchemy.*
- [ ] **Diff Query Latency:** We assume a SQL `JOIN`/`EXCEPT` between two historical timestamps is faster than parsing and diffing two massive JSON blobs in Python. *Test: Prototype the SQL diff query against a mocked DB.*
- [ ] **Frontend State Management:** We assume React can smoothly handle async lazy-loading inside the Diff View rows without causing layout thrashing or lost state. *Test: Build a mocked async API call in the `toggleExpand` function.*

## MVP Scope (Slice 1 & 2)
**In Scope:**
- Designing the PostgreSQL relational tables (`Model_Snapshot`, `Column_Snapshot`).
- Creating a targeted API endpoint (`/api/v1/projects/{id}/snapshots/compare/model`) that accepts two snapshot IDs and a `modelName`, returning the exact column diff for that model.
- Updating the React `DiffView` to fetch column data asynchronously when `toggleExpand` is triggered for a model.

**Out of Scope (For Now):**
- Full migration of all historical JSON snapshots to the new relational format.
- "Metadata Mesh" integration with downstream tools like dbt or DataHub.

## Not Doing (and Why)
- **Live Introspection (`information_schema` querying):** We are not querying the live database directly because we must maintain an immutable audit trail of what SemaBridge believed the state was at the exact moment of sync.
- **Fixing the JSON Writer:** We are not modifying the current `_extract_columns` pipeline to stuff more column data into the JSON blob, because this exacerbates the payload size issue and delays the necessary architectural shift.
- **Loading the Full Relational Diff Upfront:** We are not sending every single column change to the frontend on the initial load, to protect the UI from hanging on massive enterprise schemas.

## Open Questions
- What is the transition strategy for existing JSON-based snapshots? Do we write a migration script to populate the new tables, or do we support a fallback mechanism where old diffs continue to use the JSON dict-diff?
