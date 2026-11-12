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

- 🔄 **Fully Automated Bi-Directional Sync**: Synchronize semantic models between Snowflake, Microsoft Fabric, and local `.pbix` models.
- 📦 **Offline Local PBIX Extraction**: Ingest and deconstruct `.pbix` desktop files directly without needing Power BI Desktop, Windows, or XMLA endpoints.
- 🧠 **5-Tier DAX-to-Snowflake Translation Engine**: Deterministic AST-based translation of complex DAX (time-intelligence, hierarchies, semi-additive closing balances, non-additive dimensions) into native Snowflake Semantic View SQL.
- 📊 **Complete Semantic Fidelity**: Tables, columns, PK/FK relationships, measures, hierarchies, window metrics, and DropLedger transparency.
- 📝 **OSI Intermediate Format**: Human-readable YAML semantic layer (Open Semantic Intermediate).
- 🗄️ **DuckDB & PostgreSQL Metadata Storage**: Full audit logging, snapshot history, and version control.
- 🎨 **Modern Web UI**: React/Vite interactive dashboard with dependency graphs, run monitoring, and visual mapping tools.
- 🛡️ **Automated Pre-Deploy Quality Gates**: 5-stage automated verification (`make pre-deploy`) ensuring zero secrets, 100% bytecode compilation, and regression safety.

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
make install           # Install all dependencies (Python + npm)
make migrate           # Apply database migrations
make run               # Start FastAPI backend (port 8002)
make run-frontend      # Start Vite React frontend
make test              # Run backend pytest test suite
make pre-deploy        # Run 5-stage automated pre-deploy quality gates
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
make run          # Start Uvicorn backend (http://127.0.0.1:8002)
make run-frontend # Start Vite frontend dev server
make pre-deploy   # Run automated quality gates before pushing code

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
- Full guide: See [docs/development/setup.md](docs/development/setup.md)

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

## 📈 Real-World Benchmark Suite (13 Power BI Enterprise Models)

SemaBridge is continuously validated against an offline suite of **13 real-world Power BI desktop models (`.pbix`)** comprising 116 tables, 96 relationships, and 297 production DAX measures:

| Metric Dimension | Pre-Fix Baseline | Current Translation Suite | Status |
| :--- | :---: | :---: | :---: |
| **Total Models Audited** | 13 Models | **13 Models** | 100% Extracted Cleanly |
| **Total Extracted Tables** | 116 Tables | **116 Tables** | 100% Mapped |
| **Total Relationships** | 96 Relationships | **96 Relationships** | 100% Mapped |
| **Measures Translated** | 206 (69.4%) | **228 (76.8%)** | **+22 Business Metrics Unlocked** |
| **Dropped / Excluded** | 91 (30.6%) | **69 (23.2%)** | **All 69 Categorized (55 by-design, 14 future)** |
| **Unknown / Empty SQL Drops** | Multiple | **0 (0.0%)** | **Completely Eliminated** |

> 📖 **Full Audit Report:** See [`Docs/reports/SEMABRIDGE_13_REPORTS_TRANSLATION_AUDIT.md`](Docs/reports/SEMABRIDGE_13_REPORTS_TRANSLATION_AUDIT.md) for the complete model-by-model before-and-after audit and line-by-line classification of all 69 dropped measures.  
> 📘 **Team Onboarding Handbook:** See [`Docs/TEAM_ONBOARDING_HANDBOOK.md`](Docs/TEAM_ONBOARDING_HANDBOOK.md) for setup, architecture, and step-by-step developer workflows.

---

## 🏗️ Architecture & Structure

Semabridge enforces a strictly typed "Plugin-First" architectural pattern centering around the **OSI** representation:

```text
semabridge/
├── src/semabridge/
│   ├── core/            # Execution lifecycle, config loading, logging
│   ├── connectors/      # External integrations (Snowflake, Fabric, Local PBIX)
│   ├── converter/       # Transformation logic (e.g., TMSL ↔ SML/OSI, DAX AST Parser)
│   ├── formats/         # Format definitions & schema rules
│   ├── intermediate/    # Pydantic models for OSI / SML representation
│   ├── repository/      # DuckDB & PostgreSQL version control logic, persistence
│   ├── cli/             # Typer/Click CLI commands
│   ├── plugins/         # Extensible plugin architecture
│   └── utils/           # Shared utilities (logging, caching, sanitizers)
├── frontend/            # React/Vite-based modern Web UI
├── Tests/               # Pytest test suite (unit, integration, feature gates)
├── Docs/                # Architecture guides, onboarding handbook, and benchmark reports
└── pbix_files/          # Immutable real-world Power BI evaluation models
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

Refer to [docs/development/setup.md](docs/development/setup.md) and [docs/architecture/overview.md](docs/architecture/overview.md) for deeper internal technical mechanics.

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
