# Quality Gates Reference (`Docs/GATES.md`)

This document describes the quality gates enforced on all changes before merge or deployment.

---

## 1. Regression Gate (`Scripts/regression_gate.py`)

- **What it checks**:
  Runs the full parallel `pytest` test suite, captures failing test identifiers, compares them against [Tests/known_failures.txt](file:///Tests/known_failures.txt), and verifies that zero new test failures or regressions are introduced (`NEW REGRESSIONS: 0`).
- **How to run**:
  - **Clean Checkout Mode (Default)**:
    ```bash
    python Scripts/regression_gate.py
    ```
    Creates a temporary detached `git worktree` of `HEAD` in a clean temporary directory, executes the suite against clean tracked code, and automatically prunes the worktree afterwards so uncommitted working-tree changes never contaminate gate results.
  - **In-Place Mode (Explicit local runs)**:
    ```bash
    python Scripts/regression_gate.py --in-place
    ```
    Executes the suite directly in the current repository folder.
- **Reading results**:
  - `Known in file`: Total accepted baseline failure count in `known_failures.txt`.
  - `Failing now and known`: Tests in baseline that are still failing.
  - `Newly passing`: Tests in baseline that now pass and can be permanently removed from `known_failures.txt`.
  - `New failures (REGRESSIONS)`: Any new failure outside baseline. Must be `0` for gate to pass (exits 0 on success, 1 on regression).

---

## 2. Characterization Snapshots Gate (`Tests/characterization/test_characterization_snapshots.py`)

- **What it checks**:
  Validates 8 characterization snapshots across PBIX, Fabric, and Snowflake conversion pipelines (`osi.json`, `sml.json`, `ddl.sql`). Ensures byte-identical serialization so shared conversion and binding logic does not drift.
- **How to run**:
  ```bash
  python -m pytest Tests/characterization/test_characterization_snapshots.py
  ```
- **Reading results**:
  Must output `8 passed in ...s` with `0` failures or diffs.

---

## 3. G05 Fake Data Scan (`Tests/governance_tests/g05_fake_data_scan.py`)

- **What it checks**:
  Enforces production data integrity by statically scanning backend source code (`src/semabridge/`) and frontend components (`frontend/src/`) for:
  - F1: Hardcoded literal result rows.
  - F2: Outcome-deciding scenario switches.
  - F3: Non-empty default user inputs.
  - F4: Random or simulated mock values.
  - F5: Unredacted domain or customer business terms in literals.
  - F6: Switchable mock data flags.
- **How to run**:
  ```bash
  python Tests/governance_tests/g05_fake_data_scan.py
  ```
- **Reading results**:
  Must report `46 pass, 0 fail, 0 warn` (exit code 0).

---

## 4. Import Safety Gate (`Tests/test_import_has_no_file_side_effects.py`)

- **What it checks**:
  Dynamically imports every module across the `semabridge` package to guarantee that importing modules produces no side-effects or file modifications on tracked repository files.
- **How to run**:
  ```bash
  python -m pytest Tests/test_import_has_no_file_side_effects.py
  ```
- **Reading results**:
  `1 passed` (exit code 0).

---

## 5. Golden Gate (Snowflake Live Values) (`Scripts/golden_gate.py`)

- **What it checks**:
  Builds 3 real-world semantic models (`Competitive Marketing Analysis`, `Corporate Spend`, `Human Resources`) into temporary zero-copy sandbox clones on live Snowflake, queries the deployed semantic views, and verifies exact mathematical equivalence against 8 known ground-truth values.
- **Minimum Snowflake Grants Required for `SNOWFLAKE_ROLE`**:
  ```sql
  GRANT USAGE ON WAREHOUSE "<WAREHOUSE>" TO ROLE "<ROLE>";
  GRANT USAGE ON DATABASE "<DATABASE>" TO ROLE "<ROLE>";
  GRANT CREATE SCHEMA ON DATABASE "<DATABASE>" TO ROLE "<ROLE>";
  GRANT USAGE ON SCHEMA "<DATABASE>"."<SCHEMA>" TO ROLE "<ROLE>";
  GRANT SELECT ON ALL TABLES IN SCHEMA "<DATABASE>"."<SCHEMA>" TO ROLE "<ROLE>";
  -- Sandbox schemas created dynamically grant CREATE TABLE, CREATE VIEW, DROP SCHEMA.
  ```
  `SNOWFLAKE_ROLE` and `SNOWFLAKE_WAREHOUSE` are strictly required settings and must never fall back to user default roles or `ACCOUNTADMIN`.
- **How to run**:
  - **Local only (not in CI)**: `python Scripts/golden_gate.py` with Snowflake credentials in your own `.env` (requires a Linux / pbixray environment). Run it before a release or after changes to conversion, DDL or time intelligence.
  - It is intentionally not a GitHub Actions workflow, so no Snowflake key is stored in GitHub.
- **How to read results**:
  - A pass prints `GOLDEN GATE PASSED: All 8 golden values matched perfectly.` and exits with code 0; any mismatch prints `GOLDEN GATE FAILED` with the details and exits with code 1.
  - The temporary sandbox schemas are dropped at the end of the run, including when a check fails.
  - Without Snowflake credentials it prints `SKIPPED` and exits with code 0. That is not a pass: check that the output says `GOLDEN GATE PASSED`.

