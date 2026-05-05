# Development Workflow & Database Migrations

This guide covers local development setup, running migrations, and the automated CI/CD pipeline for schema changes.

## Local Development

### Quick Start

1. **Install Dependencies**
   ```bash
   make install
   ```

2. **Setup Python Environment**
   ```bash
   # Windows (PowerShell)
   .\.venv\Scripts\Activate.ps1
   
   # Unix/Mac (bash)
   source .venv/bin/activate
   ```

3. **Apply Database Migrations**
   ```bash
   make migrate
   ```

4. **Start Development Servers**
   ```bash
   # Terminal 1: Backend API
   make run
   
   # Terminal 2: Frontend
   make run-frontend
   ```

### Available Make Commands

```bash
make help              # Show all available commands
make install           # Install all dependencies (Python + npm)
make dev               # Show development environment setup
make migrate           # Apply latest migrations
make migrate-current   # Show current migration revision
make migrate-history   # View migration history
make run               # Start Uvicorn backend
make run-frontend      # Start Vite frontend
make lint              # Run code linters
make format            # Format code with black
make test              # Run pytest tests
make clean             # Remove artifacts
```

## Database Migrations

### Architecture

Semabridge uses **Alembic** for database schema versioning:

- **Migrations Location**: `src/semabridge/migrations/versions/`
- **Alembic Config**: `Config/alembic.ini`
- **Target DB**: PostgreSQL via SQLAlchemy + psycopg2

### Workflow: Applying Migrations

#### Local Development

```bash
# Apply all pending migrations to your local database
make migrate

# Check current revision
make migrate-current

# View all migrations
make migrate-history

# Downgrade to specific revision (if needed)
make migrate-downgrade REV=<revision-id>
```

#### Cross-Platform Scripts

For custom migration workflows:

**Unix/Mac:**
```bash
bash ./scripts/migrate.sh upgrade head
bash ./scripts/migrate.sh current
bash ./scripts/migrate.sh history
bash ./scripts/migrate.sh downgrade <revision>
```

**Windows (PowerShell):**
```powershell
.\scripts\migrate.ps1 -Command upgrade -Revision head
.\scripts\migrate.ps1 -Command current
.\scripts\migrate.ps1 -Command history
.\scripts\migrate.ps1 -Command downgrade -Revision <revision>
```

### Workflow: Creating Migrations

When adding database schema changes:

1. **Create the migration**
   ```bash
   alembic revision --autogenerate -m "Add new_column to table_name"
   ```
   This generates `src/semabridge/migrations/versions/<hash>_add_new_column_to_table_name.py`

2. **Review the generated migration**
   - Always inspect the generated file before committing
   - Ensure `upgrade()` and `downgrade()` functions are correct
   - Consider defensive patterns (e.g., `IF NOT EXISTS` for idempotency)

3. **Test locally**
   ```bash
   make migrate
   ```

4. **Commit the migration file**
   ```bash
   git add src/semabridge/migrations/versions/<hash>_*.py
   git commit -m "Add migration: <description>"
   ```

### Defensive Migration Pattern

Always make migrations idempotent (safe to run multiple times):

```python
def upgrade() -> None:
    # Good: Check before creating
    op.execute("""
        ALTER TABLE runs 
        ADD COLUMN IF NOT EXISTS sync_mode VARCHAR(20) NOT NULL DEFAULT 'copy'
    """)

def downgrade() -> None:
    # Good: Check before dropping
    op.execute("""
        ALTER TABLE runs 
        DROP COLUMN IF EXISTS sync_mode
    """)
```

## CI/CD Pipeline

### Automated Migration Workflow

When you push to `main` or `develop`, or merge a PR:

1. **Code Quality Gates** (backend lint, frontend lint, tests)
2. **Security Checks** (pip-audit, npm audit)
3. **Database Migrations** (on success of all gates)
   - Staging database migration (if secrets configured)
   - Production database migration (manual workflow dispatch option)

### GitHub Actions Configuration

**File**: `.github/workflows/apply-migrations.yml`

- **Triggers**: 
  - `push` to `main` or `develop`
  - Manual `workflow_dispatch` (select environment: staging or production)
- **Environment Secrets Required**:
  - `STAGING_DATABASE_URL` — PostgreSQL connection string for staging
  - `PRODUCTION_DATABASE_URL` — PostgreSQL connection string for production

**Setup Instructions**:

1. Go to GitHub repo **Settings → Secrets and variables → Actions**
2. Add secrets:
   - `STAGING_DATABASE_URL=postgresql://user:pass@staging-host:5432/semabridge`
   - `PRODUCTION_DATABASE_URL=postgresql://user:pass@prod-host:5432/semabridge`

### Manual Migration Trigger

To manually apply migrations to production:

1. Go to **Actions → Apply Database Migrations**
2. Click **Run workflow**
3. Select environment: `production`
4. Click **Run workflow**

The workflow will:
- Validate database connection
- Run `alembic upgrade head`
- Verify migration success
- Fail with clear error messages if anything goes wrong

## Best Practices

### 1. Always Test Migrations Locally First

```bash
# Before pushing, test:
make migrate
# Verify schema with SQL client
```

### 2. Keep Migrations Atomic

- One logical change per migration file
- Don't mix unrelated schema changes
- Bad: Alter table AND modify stored procedure in same migration
- Good: Two separate migrations

### 3. Document Complex Migrations

```python
def upgrade() -> None:
    """
    Add sync_mode column to tracks run metadata.
    
    Rationale: ExecutionEngine.execute() now accepts sync_mode parameter
    to distinguish between 'copy' vs 'append' synchronization strategies.
    
    Default: 'copy' (backward compatible with existing runs)
    """
    op.add_column('runs', sa.Column('sync_mode', sa.String(20), ...))
```

### 4. Avoid Long-Running Migrations in Production

- If a migration locks tables for > 10 seconds, consider breaking it into steps
- Add indexes concurrently: `CREATE INDEX CONCURRENTLY ...`
- Coordinate with ops team for maintenance windows

### 5. Rollback Strategy

Every migration should have a working `downgrade()` function:

```bash
# If migration fails, rollback
make migrate-downgrade REV=<previous-revision>
```

## Troubleshooting

### "alembic.ini not found"

Ensure you're in the project root directory:
```bash
cd semabridge/  # Root of repo
make migrate    # Should work
```

### "psycopg2.OperationalError: could not connect to server"

Check database connection string in `.env`:
```bash
# Verify DATABASE_URL is set
echo $DATABASE_URL  # Unix/Mac
echo $env:DATABASE_URL  # PowerShell
```

### "Alembic revision already exists"

If a migration gets interrupted mid-apply, Alembic may think it's applied when it isn't:
```bash
# Check current revision
make migrate-current

# Manually inspect database
psql -U user -d semabridge -c "SELECT version_num FROM alembic_version;"

# If stuck, mark revision as downgraded (dangerous—consult ops first)
alembic stamp <correct-revision>
```

### "Changes detected outside of target"

This warning appears when Alembic can't auto-generate the migration fully. Manually inspect and edit the migration:
```bash
# Review the generated file
cat src/semabridge/migrations/versions/<hash>_*.py

# Make manual edits if needed
vim src/semabridge/migrations/versions/<hash>_*.py

# Test
make migrate
```

## Further Reading

- [Alembic Documentation](https://alembic.sqlalchemy.org/)
- [SQLAlchemy ORM Guide](https://docs.sqlalchemy.org/en/20/)
- [PostgreSQL Docs](https://www.postgresql.org/docs/)
- Architecture: See [docs/ARCHITECTURE.md](ARCHITECTURE.md)
