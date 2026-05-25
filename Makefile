.PHONY: help install dev run migrate migrate-upgrade migrate-downgrade migrate-current migrate-history lint format clean test

# Color output
YELLOW := \033[1;33m
GREEN := \033[0;32m
CYAN := \033[0;36m
NC := \033[0m # No Color

help:
	@echo "$(CYAN)Semabridge Development Commands$(NC)"
	@echo ""
	@echo "$(YELLOW)Setup:$(NC)"
	@echo "  make install          Install dependencies (pip and npm)"
	@echo "  make dev              Activate venv and display dev environment setup"
	@echo ""
	@echo "$(YELLOW)Database:$(NC)"
	@echo "  make migrate          Apply latest database migrations (upgrade head)"
	@echo "  make migrate-upgrade  Upgrade to specific revision (make migrate-upgrade REV=<revision>)"
	@echo "  make migrate-downgrade Downgrade to specific revision (make migrate-downgrade REV=<revision>)"
	@echo "  make migrate-current  Show current database revision"
	@echo "  make migrate-history  Show migration history"
	@echo ""
	@echo "$(YELLOW)Server:$(NC)"
	@echo "  make run              Start Uvicorn dev server (port 8001)"
	@echo "  make run-frontend     Start Vite frontend dev server"
	@echo ""
	@echo "$(YELLOW)Code Quality:$(NC)"
	@echo "  make lint             Run pylint and eslint"
	@echo "  make format           Format Python code (black)"
	@echo "  make test             Run pytest test suite"
	@echo ""
	@echo "$(YELLOW)Utilities:$(NC)"
	@echo "  make clean            Remove build artifacts and cache files"
	@echo "  make help             Show this help message"
	@echo ""

install:
	@echo "$(YELLOW)Installing Python dependencies...$(NC)"
	pip install -e .
	pip install -r requirements.txt
	@echo "$(YELLOW)Installing npm dependencies...$(NC)"
	cd frontend && npm install && cd ..
	@echo "$(GREEN)✓ Dependencies installed$(NC)"

dev:
	@echo "$(YELLOW)Development environment setup:$(NC)"
	@echo ""
	@echo "1. $(CYAN)Python Environment:$(NC)"
	@echo "   On Windows (PowerShell):"
	@echo "     .\.venv\Scripts\Activate.ps1"
	@echo "   On Unix/Mac (bash/zsh):"
	@echo "     source .venv/bin/activate"
	@echo ""
	@echo "2. $(CYAN)Run migrations:$(NC)"
	@echo "     make migrate"
	@echo ""
	@echo "3. $(CYAN)Start the server:$(NC)"
	@echo "     make run"
	@echo ""
	@echo "4. $(CYAN)In another terminal, start frontend:$(NC)"
	@echo "     make run-frontend"
	@echo ""

run:
	@echo "$(YELLOW)Starting Uvicorn server on http://127.0.0.1:8001...$(NC)"
	uv run uvicorn semabridge.api.main:app --host 127.0.0.1 --port 8001 --reload --reload-dir src

run-frontend:
	@echo "$(YELLOW)Starting Vite frontend dev server...$(NC)"
	cd frontend && npm run dev

migrate:
	@echo "$(YELLOW)Applying latest database migrations...$(NC)"
ifeq ($(OS),Windows_NT)
	powershell -NoProfile -Command "& '.\scripts\migrate.ps1' -Command upgrade -Revision head"
else
	bash ./scripts/migrate.sh upgrade head
endif
	@echo "$(GREEN)✓ Migrations applied$(NC)"

migrate-upgrade:
	@echo "$(YELLOW)Upgrading to revision $(REV)...$(NC)"
ifeq ($(OS),Windows_NT)
	powershell -NoProfile -Command "& '.\scripts\migrate.ps1' -Command upgrade -Revision '$(REV)'"
else
	bash ./scripts/migrate.sh upgrade $(REV)
endif

migrate-downgrade:
	@echo "$(YELLOW)Downgrading to revision $(REV)...$(NC)"
ifeq ($(OS),Windows_NT)
	powershell -NoProfile -Command "& '.\scripts\migrate.ps1' -Command downgrade -Revision '$(REV)'"
else
	bash ./scripts/migrate.sh downgrade $(REV)
endif

migrate-current:
ifeq ($(OS),Windows_NT)
	powershell -NoProfile -Command "& '.\scripts\migrate.ps1' -Command current"
else
	bash ./scripts/migrate.sh current
endif

migrate-history:
ifeq ($(OS),Windows_NT)
	powershell -NoProfile -Command "& '.\scripts\migrate.ps1' -Command history"
else
	bash ./scripts/migrate.sh history
endif

lint:
	@echo "$(YELLOW)Running linters...$(NC)"
	pylint src/semabridge --disable=C0111,C0103 || true
	cd frontend && npm run lint || true

format:
	@echo "$(YELLOW)Formatting code...$(NC)"
	black src/ Tests/ Scripts/ --line-length=100

test:
	@echo "$(YELLOW)Running tests...$(NC)"
	pytest -v

clean:
	@echo "$(YELLOW)Cleaning build artifacts...$(NC)"
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "dist" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "build" -exec rm -rf {} + 2>/dev/null || true
	cd frontend && npm run clean 2>/dev/null || true
	@echo "$(GREEN)✓ Clean complete$(NC)"
