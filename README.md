<div align="center">

<br/>

<p align="center">
<img src="assets/images/semabridge_banner.svg" alt="SemaBridge banner" />
</p>



**Automated semantic model synchronization between Snowflake and Microsoft Fabric Power BI**

<br/>

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%20|%203.11%20|%203.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Node.js 18+](https://img.shields.io/badge/Node.js-18+-339933?style=for-the-badge&logo=nodedotjs&logoColor=white)](https://nodejs.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-F7DF1E?style=for-the-badge)](https://opensource.org/licenses/MIT)
[![React](https://img.shields.io/badge/Frontend-React%20%2F%20Vite-61DAFB?style=for-the-badge&logo=react&logoColor=black)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)

<br/>

</div>

---

## Overview

SemaBridge automates the creation and synchronization of Microsoft Fabric Power BI semantic models from Snowflake metadata — and vice versa — through a clean, versioned pipeline built around a human-readable intermediate format.

All conversions flow through the **Open Semantic Intermediate (OSI)** format and `SML` where applicable — human-readable semantic formats. Direct point-to-point conversions between sources are never permitted.

```
Source (Snowflake / Fabric)  →  Extract  →  OSI/SML (YAML)  →  Transform  →  Emit  →  Target (Fabric / Snowflake)
```

---

## Features

| | Feature | Description |
|---|---|---|
| 🔄 | **Fully Automated** | End-to-end sync with no manual modeling required |
| 📊 | **Complete Metadata** | Tables, columns, relationships, measures, hierarchies, and DAX expressions |
| 📝 | **OSI/SML Intermediate Formats** | Human-readable YAML semantic layer(s) as the universal translation layer |
| 🗄️ | **Repository Versioning** | Full state and history tracking via local persistence |
| 🎨 | **Web Dashboard** | React/Vite interface for configuration, logs, and sync control |
| 🚀 | **REST API Integration** | No XMLA endpoint required for Fabric emission |
| ⚡ | **Incremental Processing** | Local caching ensures only changed tables are reprocessed |
| 🔍 | **Auto-Detection** | Automatically detects foreign keys, date/geo patterns, and numeric measures |

---

## Table of Contents

- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Project Structure](#project-structure)
- [Development & Testing](#development--testing)
- [Contribution Guidelines](#contribution-guidelines)
- [FAQ & Troubleshooting](#faq--troubleshooting)
- [Roadmap](#roadmap)
- [License](#license)

---

## Quick Start

### Prerequisites

- **Python** 3.10, 3.11, or 3.12
- **Node.js** v18+ and npm

### 1. Clone the repository

```bash
git clone <repo-url>
cd semabridge
```

### 2. Install dependencies

**Linux / macOS**
```bash
make install
```

**Windows**
```powershell
.\dev.ps1 install
```

If the above commands fail, install manually:

```bash
# Python dependencies
uv sync
# or
pip install -e ".[dev]"

# Frontend dependencies
cd frontend && npm install && cd ..
```

### 3. Run the application

**Linux / macOS**

```bash
make run            # Start backend  →  http://127.0.0.1:8001
make run-frontend   # Start the web interface
make migrate        # Prepare the database
make help           # Show all available commands
```

**Windows**

```powershell
.\dev.ps1 run            # Start backend  →  http://127.0.0.1:8001
.\dev.ps1 run-frontend   # Start the web interface
.\dev.ps1 migrate        # Prepare the database
.\dev.ps1 help           # Show all available commands
```

> ⚠️ **Always run `migrate` after pulling new changes.** Migrations are version-controlled in `src/semabridge/migrations/versions/` and are applied automatically on production deployments.

---

## Configuration

Copy `.env.example` to `.env` and populate your credentials:

```env
# ── Snowflake ────────────────────────────────────────────────────────
SNOWFLAKE_ACCOUNT=your-account.region
SNOWFLAKE_USER=your-username
SNOWFLAKE_PASSWORD=your-password
SNOWFLAKE_WAREHOUSE=your-warehouse
SNOWFLAKE_DATABASE=your-database
SNOWFLAKE_SCHEMA=PUBLIC

# ── Microsoft Fabric ─────────────────────────────────────────────────
FABRIC_TENANT_ID=your-tenant-id
FABRIC_CLIENT_ID=your-client-id
FABRIC_CLIENT_SECRET=your-client-secret
FABRIC_WORKSPACE_ID=your-workspace-id

```

> 🔒 **Never hard-code secrets.** All credentials must be supplied via environment variables.

Note: values must be set **without surrounding quotes**:
```env
SNOWFLAKE_PASSWORD=mypassword       ✅
SNOWFLAKE_PASSWORD="mypassword"     ❌
```

---

## Project Structure

```
semabridge/
│
├── src/semabridge/
│   ├── core/            # Execution lifecycle, config loading, logging
│   ├── connectors/      # External integrations (Snowflake, Fabric)
│   ├── converter/       # Transformation logic (TMSL ↔ SML/OSI)
│   ├── formats/         # Format definitions & schema rules
│   ├── intermediate/    # Pydantic models for OSI / SML representation
│   ├── repository/      # Persistence and versioning logic
│   ├── cli/             # Typer/Click CLI commands
│   ├── plugins/         # Extensible plugin architecture
│   └── utils/           # Shared utilities (logging, caching)
│
├── frontend/            # React/Vite web dashboard
├── tests/               # Pytest test suite
└── docs/                # Extended documentation & development guides
```

The architecture follows a **Plugin-First** pattern. All logic is strongly typed and flows through the OSI/SML intermediate representation. Connectors never communicate with each other directly.

---

## Development & Testing

### Code Quality

```bash
# Formatting
black src/semabridge/
ruff check src/semabridge/

# Type checking
mypy src/semabridge/
```

### Tests

```bash
# Run full test suite with coverage report
pytest tests/ -v --cov=src/semabridge --cov-report=term-missing
```

> All contributions must maintain **≥ 80% test coverage**.

---

## Contribution Guidelines

Before contributing, please review [`docs/development/setup.md`](docs/development/setup.md) and [`docs/architecture/overview.md`](docs/architecture/overview.md).

**Core principles:**

1. **OSI/SML-First** — All conversions must flow `Source → OSI/SML` or `OSI/SML → Target`. Direct source-to-target conversion is strictly forbidden.
2. **Fail Fast** — Validate configuration at startup. Surface missing files or invalid credentials immediately.
3. **No Secrets in Code** — Credentials are always injected via environment variables, never hard-coded.
4. **Test Coverage** — All additions must meet or exceed the 80% coverage threshold.

---

## FAQ & Troubleshooting

<details>
<summary><b>Missing Credentials error</b></summary>

Ensure `.env` exists in your working directory and all required variables are set without surrounding quotes.
</details>

<details>
<summary><b>Failed to launch the web UI</b></summary>

Run `cd frontend && npm install` before starting the frontend dev server. If you are using the legacy Streamlit interface, ensure Streamlit is installed in your Python environment.
</details>

<details>
<summary><b>Database locked (<code>semabridge.db</code>)</b></summary>

The local repository enforces file locks. Wait for any other running SemaBridge process to terminate, then retry.
</details>

---

## Roadmap

- [ ] **Expanded Connectors** — Plugin endpoints for GCP BigQuery and additional proprietary models
- [ ] **Enhanced Data Transformation** — Real-time evaluation of intermediate variables during synchronization

---

## License

Distributed under the [MIT License](LICENSE).

---

<div align="center">

Made with ❤️ for the **Platform Engineering Team**

</div>
