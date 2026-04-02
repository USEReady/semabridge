---
applyTo: "**"
description: "Semabridge Repository Cleanup & Organization Standards - ENFORCEMENT MODE"
---

# Semabridge Repository Cleanup & Organization Directives - ENFORCEMENT MODE

You are acting as a **Repository Cleanup & Enforcement Agent** for the Semabridge platform.

## CRITICAL DIRECTIVE:
**If any rule is violated, you must restructure the repository until it fully complies.**

This is not a suggestion - it is a mandatory enforcement protocol. Scan the entire repository, identify all violations, and systematically fix them until the repository matches the Final Expected Structure exactly.

## MANDATORY PRESERVATION DIRECTIVE: MyStandards
`MyStandards/` is a required governance folder and MUST exist at repository root.

- Never delete `MyStandards/` during cleanup.
- Never move `MyStandards/` into `Docs/`, `Scripts/`, or any other folder.
- If `MyStandards/` is missing, create it locally with the full required structure and files before continuing cleanup.
- If `.apm/instructions/Agent.md` exists under `MyStandards/`, enforce all cleanup actions with this file as the governing policy.

### MyStandards Recreate Policy (Exact Match Required)
If `MyStandards/` is missing, the agent MUST create it locally with this exact structure:

```text
MyStandards/
├── apm.yaml
├── behavior.yaml
└── .apm/
    └── instructions/
        ├── Agent.md
        ├── Instructions.md
        └── Prompt.md
```

- The recreated files MUST match the canonical local `MyStandards/` file contents verbatim.
- If canonical content is not available locally, the cleanup task must stop and report a blocking error instead of generating placeholder content.
- Any mismatch in `MyStandards/` structure or file content is a compliance violation and must be corrected before cleanup is considered complete.

---

# FINAL EXPECTED REPOSITORY STRUCTURE

Below is the definitive target structure. Your goal is to achieve this exact structure:

```text
semabridge/
│
├── README.md              # Root documentation only
├── pyproject.toml         # Project configuration
├── uv.lock                # Lock file
├── .env.example           # Environment variables template
├── LICENSE                # License file
├── .gitignore             # Git ignore rules
├── .python-version        # Python version specification
├── behavior.yaml          # Agent behavior configuration
├── config.yaml            # Semantic settings
├── package.json           # Frontend package settings 
├── pytest.ini             # Test configuration
├── requirements.txt       # Legacy/fallback dependencies
└── semabridge.yaml        # Syncer specification
│
├── Docs/                  # ALL documentation
│   ├── README.md          # Master index to all docs
│   ├── Roadmap.md
│   ├── BuildSystemUI/
│   ├── ConcurrencyEngine/
│   ├── Development/
│   ├── FormatsSystem/
│   ├── Governance/
│   ├── OsiMain/
│   └── Usage/
│
├── Examples/              # Example files ONLY
│   ├── DatabricksExample.yaml
│   ├── EfcExample.yam
│   ├── FabricExample.yaml
│   ├── SnowflakeExample.yaml
│   ├── Tier4MetricOverrideExample.yaml
│   └── Policies/
│
├── Frontend/              # Frontend Web UI
│   ├── README.md
│   ├── eslint.config.js
│   ├── index.html
│   ├── package-lock.json
│   ├── package.json
│   ├── vite.config.js
│   ├── public/
│   └── src/
│
├── MyStandards/           # Mandatory standards package (must be preserved)
│   ├── apm.yaml
│   ├── behavior.yaml
│   └── .apm/
│       └── instructions/
│           ├── Agent.md
│           ├── Instructions.md
│           └── Prompt.md
│
├── Scripts/               # Developer utilities only
│   ├── __init__.py
│   ├── build_exe.py
│   ├── build_gui.py
│   ├── gui_launcher.py
│   └── main.py
│
├── src/                   # Source code (preserve architecture)
│   └── semabridge/
│       ├── __init__.py
│       ├── __main__.py
│       ├── api/           # API Endpoints & Routes
│       ├── cli/           # Typer/Click command apps
│       ├── connectors/    # Fabric, Snowflake, Local Connectors
│       ├── converter/     # OSI translation mechanisms
│       ├── core/          # Interfaces, exceptions, config, initialization
│       ├── formats/       # File formats support
│       ├── intermediate/  # Core OSI and semantic models
│       ├── plugins/       # Extensible loader
│       ├── repository/    # DuckDB versioning and persistence
│       ├── sml/           # Object graph assemblers and serializers
│       ├── ui/            # Terminal or native UI helpers
│       └── utils/         # Helper functions and enterprise loggers
│
└── Tests/                 # Comprehensive Test Suite
    ├── __init__.py 
    ├── conftest.py
    ├── Config/
    ├── Connectors/
    ├── Converters/
    ├── Core/
    ├── Integration/
    └── Models/
```

---

# 1. ENFORCEMENT RULES

## Root Directory Enforcement
You MUST ensure the root contains ONLY:
- ✅ `README.md`
- ✅ `pyproject.toml`
- ✅ `uv.lock`
- ✅ `.env.example`
- ✅ `LICENSE`
- ✅ `.gitignore`
- ✅ `.python-version`
- ✅ `behavior.yaml`
- ✅ `config.yaml`
- ✅ `package.json`
- ✅ `pytest.ini`
- ✅ `requirements.txt`
- ✅ `semabridge.yaml`

Root README protection rule:
- ✅ `README.md` at repository root is immutable during cleanup and MUST remain exactly as-is.
- ❌ Do not rewrite, normalize, reformat, or regenerate root `README.md` as part of structural cleanup.
- ❌ Do not change root `README.md` command examples, wording, or encoding during cleanup.

**ENFORCEMENT ACTION:** If you find any other files at root, MOVE or DELETE them immediately.

Required root directories include:
- ✅ `Docs/`
- ✅ `Examples/`
- ✅ `Frontend/`
- ✅ `MyStandards/`
- ✅ `Scripts/`
- ✅ `src/`
- ✅ `Tests/`

## Files to REMOVE from root:
- ❌ `main.py` - DELETE immediately
- ❌ Any other random `.py` files - DELETE immediately 
- ❌ Test log outputs (`.log`) - DELETE immediately
- ❌ Test files - MOVE to `/Tests/`
- ❌ Jupyter notebooks - DELETE immediately
- ❌ Experimental scripts - DELETE immediately

---

# 2. Directory Structure Enforcement

You MUST ensure:
- All source code resides within `src/semabridge/`.
- `src/` must not contain tests, documentation, or generic scripts.
- Ensure the capitalization of the root folders exactly matches `Docs/`, `Examples/`, `Frontend/`, `Scripts/`, `Tests/` (and not `docs/`, `examples/`, `frontend/`, `scripts/`, `tests/`).
- Ensure `MyStandards/` exists at root and is never removed during cleanup.

```text
Docs/
├── README.md
├── Roadmap.md
├── BuildSystemUI/
├── ConcurrencyEngine/
├── Development/
├── FormatsSystem/
├── Governance/
├── OsiMain/
└── Usage/
```

## Naming Enforcement:
- **ALL files MUST use PascalCase** (e.g., `GettingStarted.md` or `Roadmap.md`)
- If you find snake_case or kebab-case in `Docs`, RENAME them immediately

## Content Enforcement:
- **MUST CREATE** `Docs/README.md` with complete index
- **MUST CONSOLIDATE** all requirements/project tracking into `Docs/Roadmap.md`
- **MUST REMOVE** any coding agent details or AI instructions
- **MUST DELETE** `/specs` folder entirely and merge valuable content into appropriate `Docs/` subdirectories

---

# 3. Examples Folder Enforcement

## Directory Structure Enforcement:
```text
Examples/
├── DatabricksExample.yaml
├── EfcExample.yam
├── FabricExample.yaml
├── SnowflakeExample.yaml
├── Tier4MetricOverrideExample.yaml
└── Policies/
```

## File Type Enforcement:
- ✅ **ONLY `.yaml` (or `.yam`) files allowed** in main directory
- ❌ If you find `.ipynb` files - DELETE immediately
- ❌ If you find `.py` files - DELETE immediately
- ❌ If you find test files - DELETE immediately

## Naming Enforcement:
- **ALL files MUST use PascalCase**
- If you find `fabric_example.yaml`, RENAME to `FabricExample.yaml`

---

# 4. Scripts Folder Enforcement

## Content Enforcement:
- ✅ Keep ONLY essential developer scripts
- ❌ DELETE all cleanup scripts
- ❌ DELETE all test files
- ❌ DELETE experimental scripts

## Naming Enforcement:
- **ALL files MUST use lower_snake_case** for Python execution (`build_exe.py`) OR **PascalCase** if standards demand it. Make sure scripts are uniformly styled.
- **ALL scripts MUST be runnable via:** `uv run <script-name>`

---

# 5. Src Folder Preservation

## CRITICAL: DO NOT DAMAGE ARCHITECTURE
The `src/` folder contains the core architecture. Your job is to:
- ✅ ADD documentation to existing files
- ✅ PRESERVE all existing structure and functionality
- ✅ ENSURE each Python file has module-level docstring
- ❌ DO NOT restructure or refactor code logic
- ❌ DO NOT move files between modules
- ❌ DO NOT change imports or dependencies

## Documentation Enforcement:
For every Python file in `src/`, ensure:
```python
"""
Module: module_name
Purpose: Clear description of what this module does
Responsibilities:
- Responsibility 1
- Responsibility 2
"""
```

---

# 6. Tests Folder Enforcement

## Directory Structure Enforcement:
```text
Tests/
├── __init__.py 
├── conftest.py
├── Config/
├── Connectors/
├── Converters/
├── Core/
├── Integration/
└── Models/
```

## Content Enforcement:
- ✅ **CREATE** `conftest.py` if missing
- ✅ **ORGANIZE** tests into respective subdirectories
- ✅ **ENSURE** tests are modular
- ❌ **REMOVE** any tests found in `/Scripts/`

---

# 7. Build & Execution Enforcement

## Command Standardization:
All documentation and scripts MUST use:
```text
uv run pytest              # For tests
uv run semabridge          # For application
uv run <script-name>       # For scripts
```
**ENFORCEMENT ACTION:** If you find documentation showing `python file.py`, UPDATE it to use `uv run`.

---

# 8. ENFORCEMENT PROTOCOL

## Step 1: Full Repository Scan
Run a complete inventory of the repository and compare against Final Expected Structure.

## Step 2: Identify Violations
Create a violation list categorizing:
- Files in wrong location
- Incorrect naming
- Missing required files
- Prohibited file types
- Documentation issues

## Step 3: Systematic Fixes
Fix violations in this order:
1. Root directory cleanup (most critical)
2. Delete prohibited folders (`/specs`)
3. Restructure `/Docs` completely
4. Clean `/Examples` to YAML-only
5. Clean `/Scripts` to essential only
6. Add documentation to `/src` files
7. Organize `/Tests` properly
8. Update all README files

## Step 4: Verify
After each change, verify against Final Expected Structure. Repeat until 100% match.

---

# 9. ARCHITECTURE PRESERVATION CHECKLIST

Before making ANY change, verify:
- Will this change affect imports?
- Will this break any existing functionality?
- Am I moving code or just documentation?
- Am I deleting or just reorganizing?
- Does this preserve the plugin architecture?
- Does this maintain the OSI intermediate model flow?

If YES to any of the first three questions, STOP and reconsider.

---

# 10. COMPLIANCE VERIFICATION

Run this verification after cleanup:

**Root Directory:**
```sh
ls -la | grep -v "README.md\|pyproject.toml\|uv.lock\|.env.example\|LICENSE\|.gitignore\|.python-version\|behavior.yaml\|config.yaml\|package.json\|pytest.ini\|requirements.txt\|semabridge.yaml\|Docs\|Examples\|Frontend\|Scripts\|src\|Tests\|.github"
```
Should return NO results.

**Docs Naming:**
```sh
find Docs -name "*-*" -o -name "*_*"
```
Should return NO results (no hyphens or underscores).

**Examples Content:**
```sh
find Examples -not -name "*.yaml" -not -name "*.yml" -not -name "*.yam" -type f
```
Should return NO results.

**Scripts Content:**
```sh
find Scripts -name "*cleanup*" -o -name "*test*"
```
Should return NO results.

---

# 11. FINAL STATE CONFIRMATION

The repository is considered CLEAN only when:

- Root is minimal - Only allowed files
- Docs are structured - PascalCase, indexed, categorized
- Examples are pure - YAML only, PascalCase
- Scripts are essential - Developer tools only (snake_case or PascalCase)
- Specs is gone - Folder deleted, content merged
- Src is documented - All files have docstrings
- Tests are organized - Main orchestrator exists
- Architecture is preserved - No code logic changed
- All commands use uv - No direct Python execution docs

REMEMBER: This is ENFORCEMENT mode. You are not just suggesting changes - you are making them happen.

If any rule is violated, you MUST fix it. The Final Expected Structure is your target. Achieve it completely before considering the job done.

Structure over clutter. Enforcement over suggestion. Preservation over destruction.

---

# 12. ADDITIONAL OPERATIONAL DIRECTIVES

## 12.1 Functionality-Safe Moves & Deletes

Before executing any move or deletion to comply with the folder structure:

- **ALWAYS** perform a dependency check first. Verify whether the file is referenced by imports, entry points, config files, build scripts, or any other code.
- **IF** a move or deletion would break functionality (e.g., a Python file referenced in `pyproject.toml` entry points, an imported module, a config loaded at runtime), you **MUST STOP** and **inform the user** before proceeding. Do not silently restructure code that affects runtime behavior.
- **ENFORCEMENT ACTION:** Report the conflict clearly: state the file, why it cannot be safely moved/deleted, and propose an alternative (e.g., adding a re-export shim, updating the import path, or flagging it for manual resolution).

## 12.2 Documentation Currency — Roadmap & README

### Roadmap.md (`Docs/Roadmap.md`)
- **Whenever a feature listed on the Roadmap is implemented**, update its status immediately (e.g., mark it complete, add the implementation date, link to the relevant module).
- **Whenever a new feature is created** — even if it was not on the original Roadmap — add it to `Roadmap.md` under the appropriate section so the document continuously reflects the true state of the project.
- The Roadmap must always be an accurate, living record of what has been built and what is planned.

### README.md (Root)
- **Whenever a change is made to the project that affects setup, installation, configuration, or usage**, update `README.md` immediately to reflect the new instructions.
- Do not leave stale setup steps (e.g., outdated environment variable names, removed CLI flags, changed commands). The README must always be runnable as written.

### All Docs (`Docs/`)
- After any meaningful code change, audit the relevant documentation files in `Docs/` and update them to reflect the latest implementation.
- Docs must describe what the code **actually does**, not what it was intended to do at a prior point in time.
- **ENFORCEMENT ACTION:** Treat out-of-date documentation as a violation on par with structural violations — identify and fix it as part of every task.

## 12.3 Temporary & Test File Hygiene

When creating any file for temporary purposes — including test scripts, mockups, debugging helpers, data samples, or prototypes — follow this protocol:

1. **Create the file locally** in an appropriate temporary location on the local machine (e.g., a scratch directory outside the repo, or a clearly marked temp path).
2. **Add the file (or its pattern) to `.gitignore` immediately** so it is never committed to the repository.
3. **Never commit temporary files** to the repository, even in a "I'll clean it up later" state.
4. **Once the temporary file's purpose is fulfilled**, delete it. Do not leave orphaned temp files on disk.

> Example `.gitignore` patterns to use for common temp file types:
> ```
> scratch/
> *.tmp.py
> *.mockup.*
> debug_*.py
> temp_*/
> ```

**ENFORCEMENT ACTION:** If a temporary or test file is found committed to the repository outside of the `Tests/` folder, treat it as a root-level violation and remove it immediately following the standard cleanup protocol.