# Cross-platform migration runner for Windows (PowerShell)
# Usage: .\scripts\migrate.ps1 -Command upgrade -Revision head

param(
    [string]$Command = "upgrade",
    [string]$Revision = "head"
)

$ErrorActionPreference = "Stop"

# Check for alembic.ini
if (-not (Test-Path "alembic.ini")) {
    Write-Host "Error: alembic.ini not found. Run this script from the project root." -ForegroundColor Red
    exit 1
}

Write-Host "Running alembic $Command $Revision..." -ForegroundColor Yellow

switch ($Command) {
    "upgrade" {
        alembic upgrade $Revision
        Write-Host "Database upgraded to $Revision" -ForegroundColor Green
    }
    "downgrade" {
        alembic downgrade $Revision
        Write-Host "Database downgraded to $Revision" -ForegroundColor Green
    }
    "current" {
        alembic current
    }
    "history" {
        alembic history
    }
    default {
        Write-Host "Error: Unknown command: $Command" -ForegroundColor Red
        Write-Host "Usage: .\scripts\migrate.ps1 -Command [upgrade|downgrade|current|history] -Revision [revision|head]"
        exit 1
    }
}

Write-Host "Migration complete" -ForegroundColor Green
