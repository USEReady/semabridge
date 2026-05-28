"""
Full end-to-end fanout test: emit via Redis stream -> dispatcher -> Slack + Email.
Runs both dispatcher and emitter in the same process and waits for delivery.
"""
import asyncio
import os
import logging
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
)
logger = logging.getLogger("fanout_test")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

engine = create_engine(os.getenv('SEMABRIDGE_DATABASE_URL'))
SessionLocal = sessionmaker(bind=engine)

async def emit_test_event():
    """Push a Sync Completed event onto the Redis stream."""
    await asyncio.sleep(2)  # let dispatcher workers start first

    import uuid
    from semabridge.notifications.queue.redis_streams import RedisStreamsQueue
    from semabridge.notifications.models.notification_event import NotificationEvent
    from semabridge.notifications.constants import NotificationLevel, RedisQueues

    queue = RedisStreamsQueue(os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"))
    event = NotificationEvent(
        title="Sync Completed Successfully",
        message="All datasets synced without errors. Duration: 42.5s",
        level=NotificationLevel.INFO,
        source="sync_engine",
        project_id="fanout-e2e-test",
        sync_job_id=str(uuid.uuid4()),
        correlation_id=str(uuid.uuid4()),
        type="sync_result",
        payload={"sync_mode": "copy", "source_type": "fabric", "target_type": "snowflake"},
    )
    queue.enqueue(RedisQueues.NOTIFICATIONS, event.to_dict())
    logger.info("TEST EVENT EMITTED: %s (id=%s)", event.title, event.id)


async def run_dispatcher_for(seconds: int):
    """Run dispatcher and stop after given seconds."""
    db = SessionLocal()
    from semabridge.notifications.workers.dispatcher import DispatcherWorker
    worker = DispatcherWorker(
        redis_url=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
        db_session=db,
        max_workers=2,
    )
    worker.running = True

    # Start workers
    emit_task = asyncio.create_task(emit_test_event())
    dispatcher_task = asyncio.create_task(worker.start())

    # Let it run long enough to process both Slack + Email (SMTP can take 5-8s)
    await asyncio.sleep(seconds)

    # Graceful stop
    worker.running = False
    dispatcher_task.cancel()
    await asyncio.gather(emit_task, dispatcher_task, return_exceptions=True)
    db.close()


asyncio.run(run_dispatcher_for(20))
logger.info("=== FANOUT TEST COMPLETE ===")
