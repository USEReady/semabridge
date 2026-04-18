# DAX to Databricks Semantic Bridge

## Problem Statement
How Might We automate the translation of Fabric DAX semantic models into unified Databricks Metric Views, gracefully routing mathematical aggregations to YAML and offloading UI logic to auto-generated SQL lookup tables?

## Recommended Direction: "The Full Semantic Bridge"
SemaBridge will generate strict Databricks Single Metric View YAML for Category 1 DAX operations (math + filters). For Category 3 DAX operations (UI strings, conditional formatting, `SWITCH`), it will dynamically parse the cases and generate a standalone `lookup_tables_ddl.sql` file that the user can execute on Databricks to map to dimensions. 

This hybrid approach provides a true end-to-end migration path that preserves mathematical accuracy in the Semantic Layer while creating a clean fallback mechanism for UI presentation logic.

## Key Assumptions to Validate
- [ ] We can accurately parse nested `SWITCH`, `UNICHAR`, and `SELECTEDVALUE` statements effectively using our current DAX parser.
- [ ] The generated Databricks lookup tables can be easily mapped inside Databricks dimensions (joins).
- [ ] Databricks administrators are open to maintaining `_callouts` dimension tables in their BI schemas.

## MVP Scope
**In:**
- Map `SUM`, `COUNT`, `AVG` (with filters) to `CASE WHEN` SQL aggregates in the Metric View `measures` block.
- Parse `SWITCH/SELECTEDVALUE` DAX measures and output pure SQL `CREATE TABLE` / `INSERT INTO` scripts.
- Generate a `migration_report.md` specifying which UI string variables must be reformatted at the BI tool layer.

**Out:**
- Translating Power Query (`M`) transformations.
- Fully automated execution of the SQL DDL on Databricks (user will run it manually or wrap in a DAB later).
- Generating PowerBI `.pbit` templates.

## Not Doing (and Why)
- **Auto-deploying to Databricks:** Too risky and error prone for cross-account execution in MVP. We will supply the raw `.yml` and `.sql` assets instead.
- **Replacing Text formatting measures with LookML concepts:** We must remain generic for Tableau/Power BI out of the box, leaning on basic SQL strings rather than vendor-specific BI syntax.
- **Wildcard columns:** Every dimension will be explicitly prefixed as `tablealias.` to strictly obey the "3 Rules to Never Miss a Dimension."

## Open Questions
- How should the user provide the `Dates` or `Plant_Dim` definitions? Will SemaBridge mock the table schemas or query Databricks `DESCRIBE` commands live to fetch all dimension columns?
