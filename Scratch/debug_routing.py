import asyncio, os, logging
from dotenv import load_dotenv
load_dotenv()
logging.basicConfig(level=logging.DEBUG)

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from semabridge.notifications.services.routing_service import RoutingService
from semabridge.notifications.models.notification_event import NotificationEvent
from semabridge.notifications.constants import NotificationLevel

engine = create_engine(os.getenv('SEMABRIDGE_DATABASE_URL'))
SessionLocal = sessionmaker(bind=engine)
db = SessionLocal()

routing = RoutingService(db)
event = NotificationEvent(
    title='Sync Completed Successfully',
    level=NotificationLevel.INFO,
    source='sync_engine',
    project_id='test-project',
)

async def main():
    channels = await routing.get_matching_channels(event)
    print(f'=== MATCHED CHANNELS ({len(channels)}) ===')
    for ch in channels:
        print(f'  - {ch["name"]} ({ch["channel_type"]}) id={ch["id"]}')

    # Also test legacy route alone
    legacy = await routing._legacy_route(event)
    print(f'=== LEGACY CHANNELS ({len(legacy)}) ===')
    for ch in legacy:
        print(f'  - {ch["name"]} ({ch["channel_type"]})')

    # Also test rule-based routing
    rule_ids = await routing._evaluate_routing_rules(event)
    print(f'=== RULE-BASED channel_ids ({len(rule_ids)}) ===')
    for cid in rule_ids:
        print(f'  - {cid}')

    rule_channels = await routing._get_channels_by_ids(rule_ids)
    print(f'=== RULE CHANNEL OBJECTS ({len(rule_channels)}) ===')
    for ch in rule_channels:
        print(f'  - {ch["name"]} ({ch["channel_type"]}) enabled={ch.get("enabled")} status={ch.get("status")}')

asyncio.run(main())
