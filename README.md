# ðŸŒ‰ Semabridge

> *Snowflake â†” OSI â†” Fabric Semantic Model Pipeline*

[![uv run 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## âœ¨ Overview

**Semabridge** automates the creation and synchronization of Microsoft Fabric Power BI semantic models from Snowflake metadata, and vice versa. It follows a clean pipeline architecture using the Open Semantic Intermediate (OSI) format:

```text
Source (Snowflake/Fabric) â†’ Extract â†’ OSI (YAML) â†’ Transform â†’ Emit â†’ Target (Fabric/Snowflake)
```

### Key Features

- ðŸ”„ **Fully Automated**: No manual modeling required. Synchronize semantic models between platforms.
- ðŸ“Š **Complete Metadata**: Tables, columns, relationships, measures, hierarchies, and complex DAX.
- ðŸ“ **OSI Intermediate Format**: Human-readable YAML semantic layer (Open Semantic Intermediate).
- ðŸ—„ï¸ **DuckDB Version Control**: State and history tracking powered by DuckDB.
- ðŸŽ¨ **Modern Web UI**: Built-in React/Vite dashboard for configuration, logs, and synchronization control.
- ðŸš€ **REST API Integration**: No XMLA endpoint required for Fabric emission.
- âš¡ **Incremental Processing**: Only process changed tables with local caching.
- ðŸ” **Auto-Detection**: Foreign keys, date/geo patterns, and numeric measures.

---

## ðŸš€ Quick Start

### Prerequisites
- uv run 3.11
- Node.js (v18+) and npm (for the web UI)

### Installation

We use [`uv`](https://github.com/astral-sh/uv) for fast, structured dependency management.

```bash
# Clone the repository
git clone https://github.com/inarva-solutions-pvt-ltd/semabridge.git
cd semabridge

# Install uv run dependencies natively
uv sync
# (Fallback: pip install -e ".[dev]")

# Install Frontend dependencies
cd newfrontend
npm install
cd ..
```

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

**1. Launch the Backend API and Web UI**

First, start the backend API (this runs on port 8001 by default, matching the frontend proxy):
```bash
uv run uv run src/semabridge/api/main.py
```

Then, start the frontend UI:
```bash
cd newfrontend && npm run dev
```

**2. CLI Initialization & Syncing**
```bash
# Initialize Semabridge DuckDB repository
semabridge init

# Validate connections to Snowflake and Fabric
semabridge validate

# Show current configuration profile
semabridge config

# Forward Sync (Snowflake â†’ Fabric) or Reverse Sync 
# (Fabric â†’ Snowflake) based on semabridge.yaml
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

## ðŸ—ï¸ Architecture & Structure

Semabridge enforces a strictly typed "Plugin-First" architectural pattern centering around the **OSI** representation:

```text
semabridge/
â”œâ”€â”€ src/semabridge/
â”‚   â”œâ”€â”€ core/            # Execution lifecycle, config loading, logging
â”‚   â”œâ”€â”€ connectors/      # External integrations (Snowflake, Fabric)
â”‚   â”œâ”€â”€ converter/       # Transformation logic (e.g., TMSL â†” SML/OSI)
â”‚   â”œâ”€â”€ formats/         # Format definitions & schema rules
â”‚   â”œâ”€â”€ intermediate/    # Pydantic models for OSI / SML representation
â”‚   â”œâ”€â”€ repository/      # DuckDB version control logic, persistence
â”‚   â”œâ”€â”€ cli/             # Typer/Click CLI commands
â”‚   â”œâ”€â”€ plugins/         # Extensible plugin architecture
â”‚   â””â”€â”€ utils/           # Shared utilities (logging, caching)
â”œâ”€â”€ frontend/            # React/Vite-based modern Web UI
â”œâ”€â”€ tests/               # Pytest test suite
â””â”€â”€ docs/                # Extended documentation and development guides
```

---

## ðŸ”§ Development & Testing

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

## ðŸ¤ Contribution Guidelines

1. **Intermediate Model First**: All conversions are strictly `Source â†’ OSI` or `OSI â†’ Target`. Direct point-to-point conversions are strictly forbidden.
2. **Fail Fast**: Assert configuration validity at initiation (missing files, bad credentials).
3. **No Secrets in Code**: Secrets must route dynamically from environment variables, never hard-coded arguments.
4. **Test Coverage**: All code additions must meet or exceed â‰¥ 80% test coverage. 

Refer to [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for deeper internal technical mechanics.

---

## â“ FAQ & Troubleshooting

- **"Missing Credentials" Error**: Ensure the `.env` file exists in your working directory and correctly populates required variables without quotes (`SNOWFLAKE_PASSWORD`, `FABRIC_CLIENT_SECRET`, etc.).
- **Failed to launch UI (`semabridge --ui`)**: Ensure uv run Streamlit is installed (`pip install streamlit`). For the React interface, ensure NodeJS dependencies are loaded (`cd frontend && npm install`).
- **Database Locked (`semabridge.db`)**: Wait for the other CLI process to terminate. DuckDB enforces file locks.

---

## ðŸ—ºï¸ Roadmap / Future Extensions

- **Expand Connectors**: Integrations leveraging plugin endpoints for systems beyond Snowflake into GCP BigQuery or proprietary models.
- **Enhanced Data Transformation**: Real-time evaluation of intermediate variables during sync.

---

## ðŸ“œ License

[MIT License](LICENSE)

Made with â¤ï¸ for the Platform Engineering Team

