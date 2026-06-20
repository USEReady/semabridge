# Refactor: de-cruft `connection_domain_service.py` & `project_shared.py`

**Date:** 2026-06-16
**Branch:** `chore/api-services-decruft`
**Type:** dead-code / import cleanup — **no behavior change, no API-surface change**

## Why

Two service modules under `src/semabridge/api/services/` were carved out of the old
`api/main.py` monolith and carried its boilerplate with them:

- `connection_domain_service.py` (~1474 LOC)
- `project_shared.py` (~994 LOC)

Both opened with `main.py`'s docstring (`"SemaBridge FastAPI Service / Production-ready
backend…"`) and imported a pile of names that are **never referenced** in the file —
`FastAPI`, `CORSMiddleware`, `BaseHTTPMiddleware`, `Response`, the routers
(`repo_router`, `sync_router`, `account_router`, `auth_router`), `AuthMiddleware`, and
assorted unused helpers.

Two concrete problems:

1. **Layering violation** — a *service* importing *routers* is backwards
   (`api/services` → `api/<router>`).
2. **Circular import** — `project_shared.py` imported `sync_router` / `account_router`
   eagerly at module load, while those routers import `project_shared` back **lazily**
   inside functions:
   - `api/sync_router.py:354` → `from semabridge.api.services.project_shared import db_manager`
   - `api/account_router.py:305` → `import semabridge.api.services.project_shared as _ps`

   Removing the eager router imports from the service side breaks the cycle cleanly;
   the lazy imports in the routers are fine and were left untouched.

## Scope (conservative pass)

Agreed with the maintainer: remove only **never-referenced imports** and fix the
misleading docstrings. The import-time **side-effect blocks were intentionally kept**
this pass (see "Kept on purpose" below).

## What changed

### `connection_domain_service.py`

| Removed | Line(s) | Why safe |
|---|---|---|
| `FastAPI`, `BackgroundTasks` (trimmed `from fastapi import …` → `Depends, Header, Query`) | 27 | never referenced; `Depends/Header/Query` kept (used in signatures) |
| `from fastapi.middleware.cors import CORSMiddleware` | 28 | no app/middleware built here |
| `from starlette.middleware.base import BaseHTTPMiddleware` | 29 | no middleware built here |
| `from starlette.responses import Response` | 31 | never referenced (`Request` kept — used at 381/463) |
| `from semabridge.core.env import get_fabric_access_token_from_env` | 52 | never referenced |
| `from semabridge.utils.logger import setup_logging` | 56 | never referenced (call site already commented out) |
| `from semabridge.auth.fabric_validator import fabric_validator` | 58 | never referenced |
| `repo_router`, `sync_router`, `account_router` imports | 59-61 | never referenced — layering violation |
| `alert_router` (kept `install_websocket_alert_handler`) | 62 | never referenced |
| `from semabridge.api.semantic_models import SemanticSyncRequest, SemanticRefreshRequest` | 63 | never referenced |
| `try/except` importing `auth_router` + `AuthMiddleware` (+ `_AUTH_AVAILABLE`) | 81-88 | all three never referenced |
| `from datetime import datetime` | 23 | never referenced |
| `from contextlib import asynccontextmanager` | 24 | never referenced (lifespan lived in `main.py`) |
| `AsyncGenerator` (trimmed `from typing import …`) | 25 | never referenced; `Dict/Any/List/Optional` kept |
| `import hashlib`, `import re`, `import time` | 32/35/39 | no `hashlib.`/`re.`/`time.` usage in file |
| `from …sync_execution_service import execute_sync_request` | 65 | never referenced |
| Docstring | 1-9 | replaced misleading "FastAPI Service" text with an accurate module docstring |

### `project_shared.py`

| Removed | Line(s) | Why safe |
|---|---|---|
| Entire `from fastapi import FastAPI, HTTPException, Depends, Header, BackgroundTasks, Query` | 27 | none referenced |
| `CORSMiddleware`, `BaseHTTPMiddleware`, `Request`, `Response` | 28-31 | none referenced |
| `reload_settings` (trimmed `from …settings import …` → `get_settings`) | 51 | never referenced (`get_settings` kept — used at module init) |
| `from semabridge.utils.logger import setup_logging` | 54 | never referenced |
| `from semabridge.connectors.fabric_extractor import FabricExtractor` | 55 | never referenced |
| `from semabridge.auth.fabric_validator import fabric_validator` | 56 | never referenced |
| `repo_router`, `sync_router`, `account_router` imports | 57-59 | never referenced — **breaks the import cycle** |
| `alert_router` (kept `install_websocket_alert_handler`) | 60 | never referenced |
| `SemanticSyncRequest`, `SemanticRefreshRequest` | 61 | never referenced |
| `try/except` importing `auth_router` + `AuthMiddleware` (+ `_AUTH_AVAILABLE`) | 68-75 | all never referenced |
| `from contextlib import asynccontextmanager`, `AsyncGenerator` (trimmed typing) | 24-25 | never referenced |
| `import time` | 38 | no `time.` usage |
| `from …sync_execution_service import execute_sync_request` | 63 | never referenced |
| `from sqlalchemy.orm import Session`, `from semabridge.api.deps import get_db` | 65-66 | never referenced in this module |
| Docstring | 1-9 | replaced with accurate module docstring |

> Note: `from pathlib import Path as _Path` was **kept** in `project_shared.py` because
> it is reused at line 297. `import asyncio` was kept in both files (it is used by the
> Windows event-loop-policy block, which we kept).

## Kept on purpose (deferred)

These are vestigial too but touch import-time side effects, so they were left for a
later pass to keep this change zero-risk:

- The duplicated `.env` loader block (`import os as _os` + `load_dotenv`). `_os` is its
  only remaining unused import; it sits inside this kept block.
- The Windows asyncio event-loop-policy block.
- The module-level singletons `db_manager = ModelRepository()`,
  `engine = ExecutionEngine(...)`, `settings = get_settings()`, and the
  `install_websocket_alert_handler()` call.

## Why removing those imports is safe

- **In-file:** an AST audit + targeted greps confirmed each removed name has zero
  references in its file (the only remaining unused import is the intentionally-kept
  `_os`).
- **Cross-module:** none of the removed names are re-exported — no module does
  `from …connection_domain_service import <name>` / `from …project_shared import <name>`
  for any removed name. Re-exporters (`core_shared`, `core_domain_service`,
  `project_domain_service`, `connection_api_service`, `project_api_service`, the stub
  façades) only pull logic symbols / `db_manager` / `_compat_*`, which are unchanged.

## Verification performed

| Check | Result |
|---|---|
| `import connection_domain_service, project_shared` | OK — no ImportError / no circular import |
| `import semabridge.api.main` | OK — full app graph loads |
| AST unused-import audit (both files) | clean except the intentionally-kept `_os` |
| External importers of any removed name | none |
| Route inventory | **182 routes**, unchanged (registration lives in `main.py`, untouched) |

(`pytest` excluded per maintainer request for this pass.)

## Out of scope / follow-ups

- `api/services/core_shared.py` carries the same `.env` boilerplate (line 15-16).
- The duplicated module-level `db_manager`/`engine` singletons across service modules.
- The broader 4-tier `api/services/` restructure (separate, paused plan).
