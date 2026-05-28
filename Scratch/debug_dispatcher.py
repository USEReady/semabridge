"""
Debug dispatcher: why does only Slack get delivered?
"""
import asyncio
import os
import logging
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("debug_dispatcher")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

engine = create_engine(os.getenv('SEMABRIDGE_DATABASE_URL'))
SessionLocal = sessionmaker(bind=engine)
db = SessionLocal()

async def main():
    import redis as redis_module
    from semabridge.notifications.workers.dispatcher import DispatcherWorker
    from semabridge.notifications.models.notification_event import NotificationEvent
    from semabridge.notifications.constants import NotificationLevel
    from semabridge.notifications.services.routing_service import RoutingService

    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

    routing = RoutingService(db)
    event = NotificationEvent(
        title='Sync Completed Successfully',
        level=NotificationLevel.INFO,
        source='sync_engine',
        project_id='test-project-debug',
    )

    channels = await routing.get_matching_channels(event)
    print(f"\n=== get_matching_channels returned {len(channels)} channels ===")
    for ch in channels:
        print(f"  > {ch['name']} ({ch['channel_type']}) id={ch['id']}")

    # Now simulate dispatcher delivery path for each channel
    worker = DispatcherWorker(redis_url=redis_url, db_session=db)
    for ch_info in channels:
        print(f"\n=== Attempting delivery to: {ch_info['name']} ({ch_info['channel_type']}) ===", flush=True)
        try:
            await worker._deliver_to_channel(event, ch_info)
            print(f"  > _deliver_to_channel completed for {ch_info['channel_type']}")
        except Exception as ex:
            print(f"  ! Exception during _deliver_to_channel for {ch_info['channel_type']}: {ex}")
            import traceback
            traceback.print_exc()

asyncio.run(main())
