# Semabridge Development Helper for Windows
# Usage: .\dev.ps1 help

param(
    [string]$Command = "help",
    [string]$Arg = ""
)

$ErrorActionPreference = "Continue"

function Show-Help {
    Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host "    Semabridge Development Helper (Windows)" -ForegroundColor Cyan
    Write-Host "════════════════════════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "USAGE: .\dev.ps1 [command] [args]" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Setup:" -ForegroundColor Yellow
    Write-Host "  install              Install dependencies (uv first, pip fallback)"
    Write-Host "  venv                 Show venv activation instructions"
    Write-Host ""
    Write-Host "Database:" -ForegroundColor Yellow
    Write-Host "  migrate              Apply latest database migrations"
    Write-Host "  migrate-current      Show current migration revision"
    Write-Host "  migrate-history      View migration history"
    Write-Host "  migrate-upgrade REV  Upgrade to specific revision"
    Write-Host "  migrate-downgrade REV Downgrade to specific revision"
    Write-Host ""
    Write-Host "Server:" -ForegroundColor Yellow
    Write-Host "  run                  Start Uvicorn backend (http://127.0.0.1:8001)"
    Write-Host "  run-frontend         Start Vite frontend dev server"
    Write-Host ""
    Write-Host "Code Quality:" -ForegroundColor Yellow
    Write-Host "  lint                 Run linters (pylint + eslint)"
    Write-Host "  format               Format Python code (black)"
    Write-Host "  test                 Run pytest tests"
    Write-Host ""
    Write-Host "Utilities:" -ForegroundColor Yellow
    Write-Host "  clean                Remove build artifacts"
    Write-Host "  help                 Show this help message"
    Write-Host ""
}

function Check-Venv {
    if (-not (Test-Path ".\.venv\Scripts\Activate.ps1")) {
        Write-Host "Error: Virtual environment not found. Run '.\dev.ps1 install' first." -ForegroundColor Red
        exit 1
    }
}

function Install-PipDependencies {
    Write-Host "Installing Python dependencies with pip..." -ForegroundColor Yellow
    pip install -e .
    pip install -r requirements.txt
}

function Install-UvDependencies {
    Write-Host "Installing Python dependencies with uv..." -ForegroundColor Yellow
    & uv sync
    return ($LASTEXITCODE -eq 0)
}

switch ($Command.ToLower()) {
    "help" {
        Show-Help
    }
    
    "install" {
        $uvInstalled = $false

        if (Get-Command uv -ErrorAction SilentlyContinue) {
            $uvInstalled = Install-UvDependencies
        }

        if (-not $uvInstalled) {
            if (Get-Command uv -ErrorAction SilentlyContinue) {
                Write-Host "uv install failed, falling back to pip..." -ForegroundColor Yellow
            } else {
                Write-Host "uv not found, falling back to pip..." -ForegroundColor Yellow
            }

            Install-PipDependencies
        }

        Write-Host "Installing npm dependencies..." -ForegroundColor Yellow
        Set-Location frontend
        npm install
        Set-Location ..
        Write-Host "✓ Dependencies installed" -ForegroundColor Green
    }
    
    "venv" {
        Write-Host "Python Virtual Environment Setup:" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "Activate with:" -ForegroundColor Cyan
        Write-Host "  .\.venv\Scripts\Activate.ps1" -ForegroundColor White
        Write-Host ""
        Write-Host "Or run this script to activate:" -ForegroundColor Cyan
        Write-Host "  (& .\.venv\Scripts\Activate.ps1)" -ForegroundColor White
    }
    
    "migrate" {
        Check-Venv
        Write-Host "Applying latest database migrations..." -ForegroundColor Yellow
        & ".\scripts\migrate.ps1" -Command upgrade -Revision head
        Write-Host "✓ Migrations applied" -ForegroundColor Green
    }
    
    "migrate-current" {
        Check-Venv
        & ".\scripts\migrate.ps1" -Command current
    }
    
    "migrate-history" {
        Check-Venv
        & ".\scripts\migrate.ps1" -Command history
    }
    
    "migrate-upgrade" {
        Check-Venv
        if ([string]::IsNullOrWhiteSpace($Arg)) {
            Write-Host "Usage: .\dev.ps1 migrate-upgrade REVISION" -ForegroundColor Red
            exit 1
        }
        & ".\scripts\migrate.ps1" -Command upgrade -Revision $Arg
    }
    
    "migrate-downgrade" {
        Check-Venv
        if ([string]::IsNullOrWhiteSpace($Arg)) {
            Write-Host "Usage: .\dev.ps1 migrate-downgrade REVISION" -ForegroundColor Red
            exit 1
        }
        & ".\scripts\migrate.ps1" -Command downgrade -Revision $Arg
    }
    
    "run" {
        Check-Venv
        Write-Host "Starting Uvicorn server on http://127.0.0.1:8001..." -ForegroundColor Yellow
        uv run uvicorn semabridge.api.main:app --host 127.0.0.1 --port 8001 --reload --reload-dir src
    }
    
    "run-frontend" {
        Write-Host "Starting Vite frontend dev server..." -ForegroundColor Yellow
        Set-Location frontend
        npm run dev
        Set-Location ..
    }
    
    "lint" {
        Write-Host "Running linters..." -ForegroundColor Yellow
        try {
            pylint src/semabridge --disable=C0111,C0103 -f colorized 2>$null
        } catch {
            Write-Host "Pylint check complete (some warnings may appear)"
        }
        Set-Location frontend
        try {
            npm run lint 2>$null
        } catch {
            Write-Host "ESLint check complete"
        }
        Set-Location ..
    }
    
    "format" {
        Write-Host "Formatting Python code..." -ForegroundColor Yellow
        black src/ Tests/ Scripts/ --line-length=100
        Write-Host "✓ Code formatted" -ForegroundColor Green
    }
    
    "test" {
        Check-Venv
        Write-Host "Running tests..." -ForegroundColor Yellow
        pytest -v
    }
    
    "clean" {
        Write-Host "Cleaning build artifacts..." -ForegroundColor Yellow
        Get-ChildItem -Path . -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        Get-ChildItem -Path . -Recurse -File -Filter *.pyc | Remove-Item -Force -ErrorAction SilentlyContinue
        Get-ChildItem -Path . -Recurse -Directory -Filter .pytest_cache | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        Get-ChildItem -Path . -Recurse -Directory -Filter .egg-info | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "✓ Clean complete" -ForegroundColor Green
    }
    
    default {
        Write-Host "Unknown command: $Command" -ForegroundColor Red
        Write-Host ""
        Show-Help
        exit 1
    }
}
