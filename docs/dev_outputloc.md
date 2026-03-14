# Output should be where we run the code

Status: In Progress
Priority: High
Time: Short-time
assigned on : January 21, 2026

The user should be able to access the any command in the semabridge CLI from any directory in their system. 

All outputs generated will be saved in the current directory that user called semabridge from. 

 input semabridge.yaml configs will also be taken from the directory where semabridge has been called from 

project configs stored in config.yaml will be in the path where duckdb is. refer to that path in config.yaml itself. this would be set during the semabridge.init phase. create a templated config.taml during semabridge init. 

---

---

# Semabridge: CLI Execution & Environment Requirements

**Scope:** Command accessibility, Input/Output path resolution, and Configuration bootstrapping.

## 1. Execution Context Requirements

- **REQ-EXE-001 (Global Accessibility):**
    - The `semabridge` command must be executable from **any directory** in the user's shell (e.g., via adding the binary to the system `$PATH` during installation of the semabridge project).
- **REQ-EXE-002 (Current Working Directory - Inputs):**
    - When a command requires a project definition (e.g., `run`), the CLI must look for `semabridge.yaml` in the **Current Working Directory (CWD)** where the command was invoked.
    - If the file is missing in CWD, the CLI must fail immediately with a "Project not found" error. It should **not** search parent directories recursively.
- **REQ-EXE-003 (Current Working Directory - Outputs):**
    - All generated artifacts (sml/osi yaml files) must be written to the **CWD** (or a subdirectory relative to CWD specified in args).
    - The CLI must not scatter files in system folders.

## 2. Configuration & Initialization Requirements

- **REQ-CFG-001 (The "Master" Config Location):**
    - The global configuration file (`config.yaml`) must serve as the source of truth for the system state (e.g., where the database is).
    - This file must always be resolvable. Default location: `~/.semabridge/config.yaml`.
- **REQ-CFG-002 (Dynamic Repository Reference):**
    - The `config.yaml` file must contain a key `repository_path` that points to the absolute path of the DuckDB file (chosen during `init`).
    - *Reasoning:* This allows the DuckDB file to reside on a large volume or shared drive, while the config remains in the user's home directory for easy lookup.
- **REQ-CFG-003 (Templated Generation):**
    - The `semabridge init` process must not hardcode the config file creation.
    - It must load a **Config Template** (`config.tmpl.yaml`) packaged within the application, replace placeholders (like `{{REPOSITORY_PATH}}`), and write the final `config.yaml`.

---
