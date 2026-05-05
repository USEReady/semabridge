# 🌉 Semabridge

> *Snowflake ↔ OSI ↔ Fabric Semantic Model Pipeline*

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## ✨ Overview

**Semabridge** automates the creation and synchronization of Microsoft Fabric Power BI semantic models from Snowflake metadata, and vice versa. It follows a clean pipeline architecture using the Open Semantic Intermediate (OSI) format:

```text
Source (Snowflake/Fabric) → Extract → OSI (YAML) → Transform → Emit → Target (Fabric/Snowflake)
```

### Key Features

- 🔄 **Fully Automated**: No manual modeling required. Synchronize semantic models between platforms.
- 📊 **Complete Metadata**: Tables, columns, relationships, measures, hierarchies, and complex DAX.
- 📝 **OSI Intermediate Format**: Human-readable YAML semantic layer (Open Semantic Intermediate).
- 🗄️ **DuckDB Version Control**: State and history tracking powered by DuckDB.
- 🎨 **Modern Web UI**: Built-in React/Vite dashboard for configuration, logs, and synchronization control.
- 🚀 **REST API Integration**: No XMLA endpoint required for Fabric emission.
- ⚡ **Incremental Processing**: Only process changed tables with local caching.
- 🔍 **Auto-Detection**: Foreign keys, date/geo patterns, and numeric measures.

---

## 🚀 Quick Start

### Prerequisites
- Python 3.10, 3.11, or 3.12
- Node.js (v18+) and npm (for the web UI)

### Installation

We use [`uv`](https://github.com/astral-sh/uv) for fast, structured dependency management.

```bash
# Clone the repository
git clone https://github.com/inarva-solutions-pvt-ltd/semabridge.git
cd semabridge

# Install Python dependencies natively
uv sync
# (Fallback: pip install -e ".[dev]")

# Install Frontend dependencies
cd frontend
npm install
cd ..
```

### Development Setup

We provide **convenient commands** for common development tasks on all platforms:

**Linux/macOS with Make:**
```bash
make help              # Show all available commands
make install           # Install all dependencies
make migrate           # Apply database migrations
make run               # Start backend
make run-frontend      # Start frontend
```

**Windows (PowerShell) — No Make Required:**
```powershell
.\dev.ps1 help         # Show all available commands
.\dev.ps1 install      # Install all dependencies
.\dev.ps1 migrate      # Apply database migrations
.\dev.ps1 run          # Start backend
.\dev.ps1 run-frontend # Start frontend
```

These provide a **uniform interface** across Windows, macOS, and Linux. All backend + database tasks are abstracted away from OS-level complexity.

**Key Development Commands:**

```bash
# macOS/Linux
make migrate      # Apply latest database migrations
make run          # Start Uvicorn backend (http://127.0.0.1:8001)
make run-frontend # Start Vite frontend dev server

# Windows (PowerShell)
.\dev.ps1 migrate
.\dev.ps1 run
.\dev.ps1 run-frontend
```

**Database Migrations:**
- Migrations are version-controlled in `src/semabridge/migrations/versions/`
- Always run `make migrate` (or `.\dev.ps1 migrate` on Windows) after pulling new changes
- For production deployments, the CI/CD pipeline automatically applies migrations
- Cross-platform scripts: `scripts/migrate.sh` (Unix/Mac) and `scripts/migrate.ps1` (Windows)
- Full guide: See [Docs/DEVELOPMENT.md](Docs/DEVELOPMENT.md)

### Configuration

1. **Environment Variables**: Create a `.env` file (copy from `.env.example`):

```env
# Snowflake Configuration
SNOWFLAKE_ACCOUNT=your-account.region
SNOWFLAKE_USER=your-username
SNOWFLAKE_PASSWORD=your-password
SNOWFLAKE_WAREHOUSE=your-warehouse
SNOWFLAKE_DATABASE=your-database
SNOWFLAKE_SCHEMA=PUBLIC

# Fabric Configuration
FABRIC_TENANT_ID=your-tenant-id
FABRIC_CLIENT_ID=your-client-id
FABRIC_CLIENT_SECRET=your-client-secret
FABRIC_WORKSPACE_ID=your-workspace-id

# Model
MODEL_NAME=MySemanticModel
```

2. **Pipeline Settings**: Create or modify `semabridge.yaml` to define source and targets:

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

### Usage

**1. Launch the Web UI (Recommended)**
```bash
semabridge --ui
```
*Alternatively, run the frontend directly via `cd frontend && npm run dev`.*

**2. CLI Initialization & Syncing**
```bash
# Initialize Semabridge DuckDB repository
semabridge init

# Validate connections to Snowflake and Fabric
semabridge validate

# Show current configuration profile
semabridge config

# Forward Sync (Snowflake → Fabric) or Reverse Sync 
# (Fabric → Snowflake) based on semabridge.yaml
semabridge semantic sync

# Compare different models or state histories
semabridge diff compare -d <id> --from <v1> --to <v2>

# List Fabric models for easy reverse-sync discovery
semabridge list-fabric-models

# Sync complex DAX measures from Fabric to Snowflake
semabridge sync-measures --dataset-id <ID>

# View execution logs
semabridge logs list
```

*(Note: Granular commands like `extract`, `build`, `emit`, `publish` are also available for step-by-step processing pipelines.)*

---

## 🏗️ Architecture & Structure

Semabridge enforces a strictly typed "Plugin-First" architectural pattern centering around the **OSI** representation:

```text
semabridge/
├── src/semabridge/
│   ├── core/            # Execution lifecycle, config loading, logging
│   ├── connectors/      # External integrations (Snowflake, Fabric)
│   ├── converter/       # Transformation logic (e.g., TMSL ↔ SML/OSI)
│   ├── formats/         # Format definitions & schema rules
│   ├── intermediate/    # Pydantic models for OSI / SML representation
│   ├── repository/      # DuckDB version control logic, persistence
│   ├── cli/             # Typer/Click CLI commands
│   ├── plugins/         # Extensible plugin architecture
│   └── utils/           # Shared utilities (logging, caching)
├── frontend/            # React/Vite-based modern Web UI
├── tests/               # Pytest test suite
└── docs/                # Extended documentation and development guides
```

---

## 🔧 Development & Testing

```bash
# Formatting
black src/semabridge/
ruff check src/semabridge/

# Type Checking
mypy src/semabridge/

# Run tests with coverage
pytest tests/ -v --cov=src/semabridge --cov-report=term-missing
```

---

## 🤝 Contribution Guidelines

1. **Intermediate Model First**: All conversions are strictly `Source → OSI` or `OSI → Target`. Direct point-to-point conversions are strictly forbidden.
2. **Fail Fast**: Assert configuration validity at initiation (missing files, bad credentials).
3. **No Secrets in Code**: Secrets must route dynamically from environment variables, never hard-coded arguments.
4. **Test Coverage**: All code additions must meet or exceed ≥ 80% test coverage. 

Refer to [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for deeper internal technical mechanics.

---

## ❓ FAQ & Troubleshooting

- **"Missing Credentials" Error**: Ensure the `.env` file exists in your working directory and correctly populates required variables without quotes (`SNOWFLAKE_PASSWORD`, `FABRIC_CLIENT_SECRET`, etc.).
- **Failed to launch UI (`semabridge --ui`)**: Ensure Python Streamlit is installed (`pip install streamlit`). For the React interface, ensure NodeJS dependencies are loaded (`cd frontend && npm install`).
- **Database Locked (`semabridge.db`)**: Wait for the other CLI process to terminate. DuckDB enforces file locks.

---

## 🗺️ Roadmap / Future Extensions

- **Expand Connectors**: Integrations leveraging plugin endpoints for systems beyond Snowflake into GCP BigQuery or proprietary models.
- **Enhanced Data Transformation**: Real-time evaluation of intermediate variables during sync.

---

## 📜 License

[MIT License](LICENSE)

Made with ❤️ for the Platform Engineering Team
