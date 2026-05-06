# Setup and Local Development

## Prerequisites
- Python 3.11+
- Node.js 18+
- uv (recommended) or pip

## Install Dependencies

```
uv sync
cd frontend
npm install
cd ..
```

## Common Development Commands

```
# macOS/Linux
make help
make install
make migrate
make run
make run-frontend

# Windows (PowerShell)
.\dev.ps1 help
.\dev.ps1 install
.\dev.ps1 migrate
.\dev.ps1 run
.\dev.ps1 run-frontend
```

## Database Migrations

Semabridge uses Alembic for ORM-based migrations.

```
make migrate
make migrate-current
make migrate-history
```

Migration files live in src/semabridge/migrations/versions/ and the Alembic
configuration is Config/alembic.ini.
