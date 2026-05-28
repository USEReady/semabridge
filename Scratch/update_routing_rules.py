"""
Add Email channel to all relevant routing rules for multi-channel fanout.
"""
import os
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import json

engine = create_engine(os.getenv('SEMABRIDGE_DATABASE_URL'))
SessionLocal = sessionmaker(bind=engine)
db = SessionLocal()

SLACK_ID = '6fd12cd9-1457-4e43-994f-f440bc70af60'
EMAIL_ID = '881313e7-f107-47a7-8cb1-6b9381a9d1ce'

# Rules that should fan out to both Slack AND Email (general operational alerts)
FANOUT_RULES = [
    'Sync Success Alerts',
    'Sync Failure Alerts',
]

# Rules that should stay Slack-only (internal/ops-only) - left as-is:
# - Critical Slack Alerts (already says "Slack")
# - Dead Letter Queue Alerts
# - Notification Delivery Failures
# - Authentication Failures
# - Fabric Pipeline Alerts

with engine.connect() as conn:
    print("=== Current routing rules ===")
    rows = conn.execute(text("SELECT id, name, channel_ids FROM notification_routing_rules ORDER BY priority")).fetchall()
    for r in rows:
        d = dict(r._mapping)
        print(f"  [{d['name']}] channel_ids={d['channel_ids']}")

    print()
    print("=== Updating Sync Success/Failure rules to include Email ===")
    for rule_name in FANOUT_RULES:
        result = conn.execute(text(
            "SELECT id, channel_ids FROM notification_routing_rules WHERE name = :name"
        ), {"name": rule_name}).fetchone()
        if result:
            current_ids = result.channel_ids if isinstance(result.channel_ids, list) else json.loads(result.channel_ids)
            if EMAIL_ID not in current_ids:
                new_ids = current_ids + [EMAIL_ID]
                conn.execute(text(
                    "UPDATE notification_routing_rules SET channel_ids = :ids WHERE id = :id"
                ), {"ids": json.dumps(new_ids), "id": str(result.id)})
                print(f"  Updated '{rule_name}': {current_ids} -> {new_ids}")
            else:
                print(f"  '{rule_name}' already has Email channel")
        else:
            print(f"  WARNING: Rule '{rule_name}' not found!")
    
    conn.commit()

    print()
    print("=== Updated routing rules ===")
    rows = conn.execute(text("SELECT id, name, channel_ids FROM notification_routing_rules ORDER BY priority")).fetchall()
    for r in rows:
        d = dict(r._mapping)
        print(f"  [{d['name']}] channel_ids={d['channel_ids']}")
