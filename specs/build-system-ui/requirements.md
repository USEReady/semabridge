# Build System and UI - Requirements

**Feature:** UV-based Build System and Streamlit UI
**Status:** Not Started
**Priority:** Medium
**Assigned:** January 28, 2026

---

## 1. Overview

This feature adds a modern build system using `uv` for dependency management and creates a Streamlit-based graphical user interface for configuration management and version control visualization.

---

## 2. User Stories

### US-BUILD-001: Fast Dependency Management
**As a** developer  
**I want** extremely fast dependency resolution  
**So that** I can iterate quickly during development

**Acceptance Criteria:**
- Dependencies resolve in < 5 seconds
- Virtual environment creation is instant
- Lock file ensures reproducible builds
- Compatible with existing pip workflows

### US-BUILD-002: Single Executable Distribution
**As a** end user  
**I want** a single executable file  
**So that** I don't need to install Python or manage dependencies

**Acceptance Criteria:**
- Single .exe file on Windows
- Single binary on Linux/Mac
- Bundles Python interpreter and all dependencies
- File size < 100MB
- Startup time < 3 seconds

### US-BUILD-003: Visual Configuration Wizard
**As a** data engineer  
**I want** a graphical interface to configure sync jobs  
**So that** I don't have to manually edit YAML files

**Acceptance Criteria:**
- Launch UI with `semabridge --ui`
- Select source/target connectors from dropdown
- Discover available models via GUI
- Generate semabridge.yaml automatically
- Save configuration to current directory

### US-BUILD-004: Model Discovery Interface
**As a** BI analyst  
**I want** to browse available semantic models visually  
**So that** I can select which ones to sync

**Acceptance Criteria:**
- Connect to source system via UI
- Display list of available models in grid
- Filter models by name/pattern
- Select multiple models with checkboxes
- Support wildcard patterns (Sale*)

### US-BUILD-005: Version History Viewer
**As a** data governance lead  
**I want** to visualize version history  
**So that** I can track changes over time

**Acceptance Criteria:**
- Display list of all sync runs
- Show timestamp, status, and model count
- Filter by date range
- View details of specific run

### US-BUILD-006: Visual Diff Viewer
**As a** semantic model developer  
**I want** to compare two versions side-by-side  
**So that** I can understand what changed

**Acceptance Criteria:**
- Select two versions to compare
- Display side-by-side diff
- Highlight additions in green
- Highlight deletions in red
- Highlight modifications in yellow
- Support JSON and YAML formats

---

## 3. Functional Requirements

### Build System Requirements

#### REQ-BUILD-001: UV Package Management
- Project MUST use `uv` for dependency management
- `pyproject.toml` and `uv.lock` MUST be source of truth
- `uv` MUST be used for virtual environment creation
- Build process MUST be reproducible

#### REQ-BUILD-002: Single Executable Generation
- Build MUST produce standalone binary
- Binary MUST be named `SemaBridge.exe` (Windows) or `SemaBridge` (Linux/Mac)
- Binary MUST bundle:
  - Python interpreter
  - All dependencies (Snowflake, Streamlit, DuckDB, etc.)
  - Source code
  - Templates and static assets
- Build MUST use PyInstaller or Nuitka

#### REQ-BUILD-003: Local Build Process
- Build MUST run on developer machine
- No CI/CD pipeline required for this phase
- Simple command MUST trigger build: `uv run build_exe`
- Build artifacts MUST be in `dist/` directory

#### REQ-BUILD-004: Build Configuration
- Build script MUST be in `scripts/build_exe.py`
- Build options MUST be configurable
- Build MUST include all necessary data files
- Build MUST be cross-platform compatible

### UI Requirements

#### REQ-UI-001: CLI Activation
- UI MUST launch with `semabridge --ui` flag
- UI MUST start local Streamlit server
- UI MUST auto-open default browser
- UI MUST run on localhost (default port 8501)

#### REQ-UI-002: Configuration Wizard
- UI MUST dynamically load available connectors
- UI MUST support credential input (env vars or values)
- UI MUST call `connector.discover()` to list models
- UI MUST display models in selectable grid
- UI MUST support wildcard pattern input
- UI MUST generate valid `semabridge.yaml`
- UI MUST allow saving config to disk

#### REQ-UI-003: Model Selection Interface
- UI MUST display checkboxes for model selection
- UI MUST support multi-select
- UI MUST show model metadata (size, last modified, etc.)
- UI MUST support search/filter
- UI MUST validate selections before generating config

#### REQ-UI-004: Repository Explorer
- UI MUST connect to local DuckDB repository
- UI MUST display list of past runs
- UI MUST show run status (Success/Fail)
- UI MUST show timestamp and run ID
- UI MUST support filtering by date/status
- UI MUST allow viewing run details

#### REQ-UI-005: Version Comparison
- UI MUST allow selecting two versions
- UI MUST display side-by-side diff
- UI MUST highlight changes with colors:
  - Green: Additions
  - Red: Deletions
  - Yellow: Modifications
- UI MUST support JSON and YAML formats
- UI MUST allow exporting diff report

#### REQ-UI-006: Professional Design
- UI MUST use minimalist, enterprise-grade design
- UI MUST use high-contrast colors
- UI MUST use standard data grids
- UI MUST NOT use cartoons, mascots, or playful animations
- UI MUST use professional color palette (Blues, Grays)
- UI MUST be responsive and intuitive

#### REQ-UI-007: Progress Indicators
- UI MUST show spinner for long-running operations
- UI MUST display progress bars where applicable
- UI MUST provide clear feedback for user actions
- UI MUST handle errors gracefully with clear messages

---

## 4. Non-Functional Requirements

### NFR-BUILD-001: Build Performance
- Full build MUST complete in < 5 minutes
- Incremental build MUST complete in < 1 minute
- Dependency resolution MUST complete in < 10 seconds

### NFR-BUILD-002: Executable Size
- Executable size MUST be < 100MB
- Startup time MUST be < 3 seconds
- Memory footprint MUST be < 500MB at idle

### NFR-UI-001: UI Performance
- UI MUST load in < 2 seconds
- Model discovery MUST complete in < 10 seconds
- Diff comparison MUST render in < 1 second
- UI MUST remain responsive during operations

### NFR-UI-002: UI Usability
- UI MUST be usable without training
- UI MUST provide tooltips for all controls
- UI MUST have clear navigation
- UI MUST support keyboard shortcuts

### NFR-UI-003: UI Reliability
- UI MUST handle connection failures gracefully
- UI MUST validate all user inputs
- UI MUST prevent invalid configurations
- UI MUST auto-save work in progress

---

## 5. Constraints

- Build system must work on Windows, Linux, and macOS
- UI must work in modern browsers (Chrome, Firefox, Safari, Edge)
- UI must not require internet connection (except for connector operations)
- Executable must be self-contained (no external dependencies)

---

## 6. Assumptions

- Users have modern browsers installed
- Users have sufficient disk space for executable (200MB)
- Users have permission to run executables
- Network connectivity is available for connector operations

---

## 7. Dependencies

- `uv` package manager (by Astral)
- PyInstaller or Nuitka for executable generation
- Streamlit for UI framework
- Rich library for terminal UI
- DuckDB for state management

---

## 8. Open Questions

1. Should we support custom themes in the UI?
2. Should we bundle multiple executables (CLI-only vs CLI+UI)?
3. How do we handle updates to the executable?
4. Should we support plugins in the executable?
5. Should we add authentication to the UI?

---

## 9. Out of Scope

- Web-based deployment (cloud hosting)
- Multi-user support
- Real-time collaboration
- Mobile app
- Browser extension
- Auto-update mechanism (for this phase)
