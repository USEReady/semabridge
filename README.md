<div align="center">

<br/>

<p align="center">

<img src="semabridge_banner.svg" alt="SemaBridge banner" />

</p>



[![Python 3.10+](https://img.shields.io/badge/Python-3.10%20|%203.11%20|%203.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Node.js 18+](https://img.shields.io/badge/Node.js-18+-339933?style=for-the-badge&logo=nodedotjs&logoColor=white)](https://nodejs.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-F7DF1E?style=for-the-badge)](https://opensource.org/licenses/MIT)
[![React](https://img.shields.io/badge/Frontend-React%20%2F%20Vite-61DAFB?style=for-the-badge&logo=react&logoColor=black)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)

<br/>

> **Semabridge** automates the creation and synchronization of **Microsoft Fabric Power BI semantic models** from Snowflake metadata — and vice versa — through a clean, versioned pipeline built around a human-readable intermediate format.

<br/>

</div>

---

## 📖 Table of Contents

- [Overview](#-overview)
- [Pipeline Architecture](#-pipeline-architecture)
- [Key Features](#-key-features)
- [Quick Start](#-quick-start)
- [Configuration](#-configuration)
- [Project Structure](#-project-structure)
- [Development & Testing](#-development--testing)
- [Contribution Guidelines](#-contribution-guidelines)
- [FAQ & Troubleshooting](#-faq--troubleshooting)
- [Roadmap](#-roadmap)
- [License](#-license)

---

## 🌟 Overview

Semabridge removes all manual effort from semantic modeling across data platforms. Whether you're pushing Snowflake metadata into a Fabric Power BI model or reverse-engineering an existing Fabric dataset into Snowflake, the pipeline handles every step — tables, columns, relationships, measures, hierarchies, and complex DAX expressions — with full versioning and a modern web dashboard.

---

## 🔄 Pipeline Architecture

All conversions strictly flow through the **Open Semantic Intermediate (OSI)** format — a human-readable YAML semantic layer. Direct point-to-point conversions are **never** allowed.

<p align="center"><code>Source (Snowflake/Fabric) → Extract → OSI (YAML) → Transform → Emit → Target (Fabric/Snowflake)</code></p>

<p align="center">Version control is handled in the local repository layer.</p>

---

## ✨ Key Features

| Feature | Description |
| --- | --- |
| 🔄 **Fully Automated** | No manual modeling. Sync semantic models between Snowflake and Fabric end-to-end. |
| 📊 **Complete Metadata** | Tables, columns, relationships, measures, hierarchies, and complex DAX expressions. |
| 📝 **OSI Intermediate Format** | Human-readable YAML semantic layer acts as a universal translation layer. |
| 🗄️ **Repository Versioning** | Full state and history tracking via local persistence. |
| 🎨 **Modern Web UI** | Built-in React/Vite dashboard for configuration, logs, and sync control. |
| 🚀 **REST API Integration** | No XMLA endpoint required for Fabric emission. |
| ⚡ **Incremental Processing** | Local caching ensures only changed tables are reprocessed. |
| 🔍 **Auto-Detection** | Automatically detects foreign keys, date/geo patterns, and numeric measures. |

---

## 🚀 Quick Start

### Prerequisites

- **Python** 3.10, 3.11, or 3.12
- **Node.js** v18+ and npm *(for the web UI)*

### 1 — Clone & Install

```bash
git clone <repo-url>
cd semabridge
```

Choose the installation method that fits your workflow:

```bash
# Option A — Editable install (recommended for development)
pip install -e ".[dev]"

# Option B — uv (if you have it installed)
uv sync
```

Then install the frontend dependencies:

```bash
cd frontend
npm install
cd ..
```

### 2 — Start the Development Servers

Semabridge provides cross-platform convenience scripts so every command works identically on Windows, macOS, and Linux.

<details>
<summary><b>🐧 Linux / macOS — Make</b></summary>

```bash
make help           # Show all available commands
make install        # Install all dependencies
make migrate        # Apply database migrations
make run            # Start backend  →  http://127.0.0.1:8001
make run-frontend   # Start Vite frontend dev server
```
</details>

<details>
<summary><b>🪟 Windows — PowerShell</b></summary>

```powershell
.\dev.ps1 help           # Show all available commands
.\dev.ps1 install        # Install all dependencies
.\dev.ps1 migrate        # Apply database migrations
.\dev.ps1 run            # Start backend  →  http://127.0.0.1:8001
.\dev.ps1 run-frontend   # Start Vite frontend dev server
```
</details>

> ⚠️ **Always run `migrate` after pulling new changes.** Migrations are version-controlled in `src/semabridge/migrations/versions/` and CI/CD applies them automatically on production deployments.

---

## ⚙️ Configuration

### Environment Variables

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

# ── Model ─────────────────────────────────────────────────────────────
MODEL_NAME=MySemanticModel
```

> 🔒 **Never hard-code secrets.** All credentials must be supplied via environment variables.

### Pipeline Settings (`semabridge.yaml`)

Define your source and target in `semabridge.yaml`:

```yaml
source:
  type: fabric
  dataset_id: "example-dataset-id"
  workspace_id: "example-workspace-id"

target:
  type: snowflake
  deploy: true

model_name: "Customer Profitability"
```

---

## 🏗️ Project Structure

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

The architecture follows a **Plugin-First** pattern. All logic is strongly typed and flows through the OSI intermediate representation — connectors never communicate with each other directly.

---

## 🔧 Development & Testing

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
# Run full suite with coverage report
pytest tests/ -v --cov=src/semabridge --cov-report=term-missing
```

> All code contributions must maintain **≥ 80% test coverage**.

---

## 🤝 Contribution Guidelines

Before contributing, please review [docs/development/setup.md](docs/development/setup.md) and [docs/architecture/overview.md](docs/architecture/overview.md).

**Core principles:**

1. **OSI-First** — All conversions must go `Source → OSI` or `OSI → Target`. Direct source-to-target conversion is strictly forbidden.
2. **Fail Fast** — Validate configuration at startup. Surface missing files or bad credentials immediately.
3. **No Secrets in Code** — Credentials are always injected via environment variables, never hard-coded.
4. **Test Coverage** — All additions must meet or exceed the 80% coverage threshold.

---

## ❓ FAQ & Troubleshooting

<details>
<summary><b>"Missing Credentials" Error</b></summary>

Ensure `.env` exists in your working directory and all required variables are set **without surrounding quotes**. For example:

```env
SNOWFLAKE_PASSWORD=mypassword       ✅
SNOWFLAKE_PASSWORD="mypassword"     ❌
```
</details>

<details>
<summary><b>Failed to launch the web UI</b></summary>

- For the legacy Streamlit interface: install Streamlit in your Python environment.
- For the React interface: run `cd frontend && npm install` before starting the frontend dev server.
</details>

<details>
<summary><b>Database Locked (<code>semabridge.db</code>)</b></summary>

The local repository enforces file locks. Wait for any other running Semabridge process to terminate, then retry.
</details>

---

## 🗺️ Roadmap

- [ ] **Expanded Connectors** — Plugin endpoints for GCP BigQuery and additional proprietary models
- [ ] **Enhanced Data Transformation** — Real-time evaluation of intermediate variables during synchronization

---

## 📜 License

Distributed under the [MIT License](LICENSE).

---

<div align="center">

Made with ❤️ for the **Platform Engineering Team**

</div>
