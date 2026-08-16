"""Demo-mode activation gate.

Demo mode masks a genuinely failed run's *display* to the API/UI so a live
demo can navigate a clean "success" path end-to-end, without ever losing the
true outcome (that always lands in the ``runs`` table and logs — see
``ModelRepository.record_run_complete`` and ``core.run_helpers.mask_run_for_display``)
and without ever changing what actually gets deployed (see
``core/engine/finalize.py``'s staged-artifact promotion, which only ever
promotes/deploys on a genuine success, demo mode or not).

Activation requires BOTH of the following, deliberately an AND rather than
an OR:

1. The server process itself was started with ``SEMABRIDGE_DEMO_MODE=true``
   — a conscious, ops-level decision for *this* running server.
2. The specific project being run has ``behavior.features.demo_mode: true``
   in its own project YAML — a conscious, per-project decision.

A project YAML with a stray ``demo_mode: true`` left in it does nothing on a
server that was never started in demo mode, and the env var alone does not
mask every project on that server — only ones that opted in individually.
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
    """Effective demo_mode for a run = server armed AND project opted in.

    Both gates must be true — this is intentional. A project config
    accidentally left with ``demo_mode: true`` must not activate masking on
    a server that never set the env var, and the env var alone must not
    mask every project's runs on that server.
    """
    return demo_mode_globally_armed() and bool(project_flag)
