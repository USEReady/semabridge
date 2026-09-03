from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from semabridge.api.services.core_domain_service import health_check

router = APIRouter()
router.get('/api/health')(health_check)


@router.get('/api/health/live')
async def liveness():
    """Kubernetes/Docker liveness probe — confirms the process is alive."""
    return {"status": "ok"}


@router.get('/api/health/checkout')
async def checkout_identity():
    """Answers "which on-disk checkout is this running process actually
    executing?" -- an in-process self-report, not an inference from PID/
    port/OS process table (any of which a stray PYTHONPATH, a wrong venv,
    or a stale editable-install pointer can make misleading).

    This machine has more than one local checkout of this package sharing
    the name "semabridge"; `pip install -e .` from any of them silently
    repoints the SAME global editable-install registration, so a bare
    `python` command elsewhere on the machine can end up importing a
    different checkout's code while still calling itself "semabridge" and
    still answering on the port you expect. Before trusting anything this
    server returns, confirm `package_root` below is the checkout you
    intended to run -- e.g. exactly this repo's `src\\semabridge`.
    """
    import os
    import subprocess
    import sys

    import semabridge

    package_file = os.path.abspath(semabridge.__file__)
    package_root = os.path.dirname(package_file)
    repo_root = os.path.dirname(os.path.dirname(package_root))

    git_commit = None
    git_branch = None
    git_dirty = None
    try:
        git_commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=repo_root, stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
        git_branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root, stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
        status_out = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=repo_root, stderr=subprocess.DEVNULL, timeout=5,
        ).decode()
        git_dirty = bool(status_out.strip())
    except Exception:
        pass  # Not a git checkout, git not on PATH, or repo_root guess was wrong -- non-fatal.

    return {
        "package_file": package_file,
        "package_root": package_root,
        "repo_root_guess": repo_root,
        "python_executable": sys.executable,
        "pid": os.getpid(),
        "cwd": os.getcwd(),
        "git_commit": git_commit,
        "git_branch": git_branch,
        "git_dirty": git_dirty,
    }


@router.get('/api/health/ready')
async def readiness():
    """Kubernetes/Docker readiness probe — confirms the app can serve traffic."""
    from semabridge.repository.orm.session_factory import db_manager

    checks: dict = {}
    try:
        with db_manager.get_session() as session:
            session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={"status": "ready" if all_ok else "degraded", "checks": checks},
    )
