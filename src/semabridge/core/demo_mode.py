"""Demo-mode activation gate.

Demo mode masks a genuinely failed run's *display* to the API/UI so a live
demo can navigate a clean "success" path end-to-end, without ever losing the
true outcome (that always lands in the ``runs`` table and logs — see
``ModelRepository.record_run_complete`` and ``core.run_helpers.mask_run_for_display``)
and without ever changing what actually gets deployed (see
``core/engine/finalize.py``'s staged-artifact promotion, which only ever
promotes/deploys on a genuine success, demo mode or not).

Activation requires only the server-level environment variable:

1. The server process itself was started with ``SEMABRIDGE_DEMO_MODE=true``
   — a conscious, ops-level decision for *this* running server.

When this env var is set, demo mode is globally enabled for all projects.
"""

from __future__ import annotations

import os


def demo_mode_globally_armed() -> bool:
    """Whether this server process was started with demo mode armed.

    Master kill-switch: off unless explicitly set. Follows the same
    env-var-gate pattern as ``AUTH_ENABLED`` (see
    ``api/services/core_sync_impl.py``).
    """
    return os.environ.get("SEMABRIDGE_DEMO_MODE", "").strip().lower() == "true"


def resolve_effective_demo_mode(project_flag: bool) -> bool:
    """Effective demo_mode for a run = server armed (project_flag is ignored).

    Only the server-level env var matters. The project_flag parameter is
    kept for backwards compatibility but is not used.
    """
    return demo_mode_globally_armed()
