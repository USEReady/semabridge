# Build System and UI - Implementation Tasks

**Feature:** UV-based Build System and Streamlit UI
**Status:** Not Started

---

## Task List

- [ ] 1. UV Build System Setup
  - [ ] 1.1 Update pyproject.toml with UV configuration
  - [ ] 1.2 Add Streamlit and PyInstaller to dependencies
  - [ ] 1.3 Create uv.lock file
  - [ ] 1.4 Test UV virtual environment creation
  - [ ] 1.5 Document UV installation and usage

- [ ] 2. Build Script Development
  - [ ] 2.1 Create scripts/build_exe.py
  - [ ] 2.2 Implement platform detection
  - [ ] 2.3 Implement build directory cleanup
  - [ ] 2.4 Configure PyInstaller options
  - [ ] 2.5 Add data file inclusion (UI, formats, templates)
  - [ ] 2.6 Test build on Windows
  - [ ] 2.7 Test build on Linux
  - [ ] 2.8 Test build on macOS

- [ ] 3. Executable Optimization
  - [ ] 3.1 Minimize executable size
  - [ ] 3.2 Optimize startup time
  - [ ] 3.3 Test executable performance
  - [ ] 3.4 Add version information to executable

- [ ] 4. UI Foundation
  - [ ] 4.1 Create src/semabridge/ui/ directory structure
  - [ ] 4.2 Create main UI app (app.py)
  - [ ] 4.3 Implement page routing
  - [ ] 4.4 Add custom CSS for professional styling
  - [ ] 4.5 Create sidebar navigation

- [ ] 5. Configuration Wizard Page
  - [ ] 5.1 Create configuration_wizard.py
  - [ ] 5.2 Implement source connector selection
  - [ ] 5.3 Implement Fabric source configuration form
  - [ ] 5.4 Implement Snowflake source configuration form
  - [ ] 5.5 Implement target connector selection
  - [ ] 5.6 Implement model discovery integration
  - [ ] 5.7 Implement model selection grid
  - [ ] 5.8 Add search/filter functionality
  - [ ] 5.9 Implement YAML generation
  - [ ] 5.10 Implement configuration save to disk

- [ ] 6. Version Explorer Page
  - [ ] 6.1 Create version_explorer.py
  - [ ] 6.2 Implement DuckDB connection
  - [ ] 6.3 Implement run history view
  - [ ] 6.4 Add date range filter
  - [ ] 6.5 Add status filter
  - [ ] 6.6 Implement version selection interface
  - [ ] 6.7 Integrate semantic diff engine
  - [ ] 6.8 Implement side-by-side diff view
  - [ ] 6.9 Add color-coded change highlighting
  - [ ] 6.10 Implement diff export functionality

- [ ] 7. CLI Integration
  - [ ] 7.1 Add --ui flag to main CLI
  - [ ] 7.2 Implement UI launch logic
  - [ ] 7.3 Configure Streamlit server options
  - [ ] 7.4 Test UI launch from executable
  - [ ] 7.5 Add error handling for UI launch failures

- [ ] 8. Model Discovery Integration
  - [ ] 8.1 Integrate Fabric connector discovery
  - [ ] 8.2 Integrate Snowflake connector discovery
  - [ ] 8.3 Add error handling for connection failures
  - [ ] 8.4 Implement progress indicators
  - [ ] 8.5 Add model metadata display

- [ ] 9. Settings Page
  - [ ] 9.1 Create settings page
  - [ ] 9.2 Display global configuration
  - [ ] 9.3 Allow configuration editing
  - [ ] 9.4 Implement configuration validation
  - [ ] 9.5 Add save functionality

- [ ] 10. UI Testing
  - [ ] 10.1 Write unit tests for configuration wizard
  - [ ] 10.2 Write unit tests for version explorer
  - [ ] 10.3 Write integration tests for UI pages
  - [ ] 10.4 Test UI responsiveness
  - [ ] 10.5 Test error handling
  - [ ] 10.6 Perform usability testing

- [ ] 11. Build Testing
  - [ ] 11.1 Test executable on Windows 10/11
  - [ ] 11.2 Test executable on Ubuntu 20.04/22.04
  - [ ] 11.3 Test executable on macOS 12+
  - [ ] 11.4 Verify all dependencies are bundled
  - [ ] 11.5 Test UI launch from executable
  - [ ] 11.6 Measure executable size and startup time

- [ ] 12. Documentation
  - [ ] 12.1 Write build system user guide
  - [ ] 12.2 Document UV setup and usage
  - [ ] 12.3 Create UI user guide with screenshots
  - [ ] 12.4 Document configuration wizard workflow
  - [ ] 12.5 Document version explorer features
  - [ ] 12.6 Create troubleshooting guide
  - [ ] 12.7 Add developer guide for UI extensions

---

## Task Details

### 1.1 Update pyproject.toml
**Description:** Add UV-specific configuration and Streamlit dependencies.

**Files:**
- `pyproject.toml`

**Changes:**
- Add `[tool.uv]` section
- Add Streamlit to dependencies
- Add PyInstaller to dev dependencies
- Update project scripts

**Acceptance Criteria:**
- UV can install dependencies
- Lock file is generated
- All dependencies resolve correctly

---

### 2.1 Create build script
**Description:** Implement PyInstaller-based build script.

**Files:**
- `scripts/build_exe.py`

**Features:**
- Platform detection
- Clean build directories
- Configure PyInstaller
- Include data files
- Error handling

**Acceptance Criteria:**
- Script runs without errors
- Produces executable in dist/
- Executable is self-contained
- Build completes in < 5 minutes

---

### 5.2 Implement source connector selection
**Description:** Create dropdown for selecting source system.

**Files:**
- `src/semabridge/ui/pages/configuration_wizard.py`

**Components:**
- Dropdown with Snowflake, Fabric, Databricks options
- Dynamic form rendering based on selection
- Credential input fields
- Validation

**Acceptance Criteria:**
- Dropdown displays all options
- Form updates when selection changes
- Credentials are validated
- Error messages are clear

---

### 6.8 Implement side-by-side diff view
**Description:** Create visual diff comparison interface.

**Files:**
- `src/semabridge/ui/pages/version_explorer.py`

**Features:**
- Two-column layout
- Color-coded changes (green/red/yellow)
- Expandable change items
- JSON/YAML formatting
- Export functionality

**Acceptance Criteria:**
- Diff renders correctly
- Colors are applied properly
- Changes are clearly visible
- Export works for all formats

---

### 11.1 Test executable on Windows
**Description:** Comprehensive testing of Windows executable.

**Test Cases:**
- Launch executable
- Run CLI commands
- Launch UI
- Test all UI features
- Verify no missing dependencies
- Check startup time
- Measure memory usage

**Acceptance Criteria:**
- All tests pass
- No errors or warnings
- Performance meets targets
- UI is fully functional

---

## Dependencies

- Task 1.x must be completed before 2.x
- Task 2.x must be completed before 3.x and 11.x
- Task 4.x must be completed before 5.x, 6.x, 9.x
- Task 5.x and 6.x must be completed before 10.x
- Task 7.x depends on 4.x
- Task 11.x depends on 2.x and 7.x

---

## Estimated Effort

| Task Group | Estimated Hours |
|------------|----------------|
| 1. UV Setup | 6 hours |
| 2. Build Script | 12 hours |
| 3. Optimization | 8 hours |
| 4. UI Foundation | 10 hours |
| 5. Config Wizard | 20 hours |
| 6. Version Explorer | 18 hours |
| 7. CLI Integration | 6 hours |
| 8. Discovery Integration | 10 hours |
| 9. Settings Page | 8 hours |
| 10. UI Testing | 16 hours |
| 11. Build Testing | 12 hours |
| 12. Documentation | 14 hours |
| **Total** | **140 hours** |

---

## Success Criteria

- [ ] Executable builds successfully on all platforms
- [ ] Executable size < 100MB
- [ ] Startup time < 3 seconds
- [ ] UI launches without errors
- [ ] All UI features work correctly
- [ ] Configuration wizard generates valid YAML
- [ ] Version explorer displays diffs correctly
- [ ] All tests pass
- [ ] Documentation is complete
- [ ] User feedback is positive
