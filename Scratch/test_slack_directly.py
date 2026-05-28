import os
import json
import asyncio
from dotenv import load_dotenv
load_dotenv()

from semabridge.repository.orm.session_factory import db_manager
from semabridge.notifications.models import NotificationChannel
from semabridge.notifications.utils.crypto import NotificationCrypto
from semabridge.notifications.adapters.slack_adapter import SlackAdapter

async def main():
    try:
        # 1. Fetch channel config
        with db_manager.get_session() as session:
            channel = session.query(NotificationChannel).filter(
                NotificationChannel.name == "My slack workspace"
            ).first()
            if not channel:
                print("Error: Slack channel config not found in DB.")
                return
            
            # Decrypt config_json
            crypto = NotificationCrypto()
            decrypted_str = crypto.decrypt(channel.config_json)
            config_json = json.loads(decrypted_str)
            print("Successfully loaded Slack configuration.")

        # 2. Build a valid, clean Slack Block Kit payload
        payload = {
            "text": "✅ Sync Completed Successfully",
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "✅ Sync Completed Successfully",
                        "emoji": True
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Synchronization completed successfully on **sync_engine**.\n\n━━━━━━━━━━━━━━\n📌 *Project:* competative-sync-notif\n🔧 *Source:* sync_engine\n🆔 *Job ID:* run-debug-notif-success-123"
                    }
                }
            ]
        }

        # 3. Send using SlackAdapter
        adapter = SlackAdapter(config_json)
        try:
            print("Invoking SlackAdapter.send()...")
            result = await adapter.send(payload, config_json)
            print("=====================================================")
            print(f"Result returned to dispatcher: {result}")
            print("=====================================================")
        finally:
            await adapter.close()

    except Exception as e:
        print(f"Direct Slack test exception: {e}")

if __name__ == "__main__":
    asyncio.run(main())
