"""
Temporal Worker Bootstrap for Semabridge.

Starts a Temporal Python SDK worker that polls the ``semabridge-sync``
task queue and executes the registered workflows and activities.

Usage (standalone):
    python -m semabridge.orchestration.temporal.worker

Environment variables:
    TEMPORAL_HOST    – Temporal Frontend address  (default: localhost:7233)
    TEMPORAL_NAMESPACE – Temporal namespace        (default: default)
    TASK_QUEUE       – Task queue name             (default: semabridge-sync)
    MAX_CONCURRENT_ACTIVITIES – Worker concurrency (default: 10)
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from typing import List

from temporalio.client import Client
from temporalio.worker import Worker

from semabridge.orchestration.temporal.workflows import (
    SyncModelWorkflow,
    BatchSyncWorkflow,
)
from semabridge.orchestration.temporal.activities import (
    extract_metadata,
    compute_diff,
    emit_to_fabric,
    persist_snapshot,
)


# ---------------------------------------------------------------------------
# Configuration from env
# ---------------------------------------------------------------------------

TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "localhost:7233")
TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "default")
TASK_QUEUE = os.getenv("TASK_QUEUE", "semabridge-sync")
MAX_CONCURRENT_ACTIVITIES = int(os.getenv("MAX_CONCURRENT_ACTIVITIES", "10"))


# ---------------------------------------------------------------------------
# Worker lifecycle
# ---------------------------------------------------------------------------


async def run_worker() -> None:
    """Connect to Temporal and start polling the task queue."""
    client = await Client.connect(
        TEMPORAL_HOST,
        namespace=TEMPORAL_NAMESPACE,
    )

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[SyncModelWorkflow, BatchSyncWorkflow],
        activities=[
            extract_metadata,
            compute_diff,
            emit_to_fabric,
            persist_snapshot,
        ],
        max_concurrent_activities=MAX_CONCURRENT_ACTIVITIES,
    )

    # Graceful shutdown on SIGINT / SIGTERM
    shutdown_event = asyncio.Event()

    def _request_shutdown(*_args: object) -> None:
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except NotImplementedError:
            # Windows does not support add_signal_handler for SIGTERM
            signal.signal(sig, _request_shutdown)

    print(
        f"Semabridge Temporal worker started  "
        f"queue={TASK_QUEUE}  host={TEMPORAL_HOST}  "
        f"concurrency={MAX_CONCURRENT_ACTIVITIES}"
    )

    async with worker:
        await shutdown_event.wait()

    print("Worker shut down gracefully.")


def main() -> None:
    """Entry point for ``python -m semabridge.orchestration.temporal.worker``."""
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
