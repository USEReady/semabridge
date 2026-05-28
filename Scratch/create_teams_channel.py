import json
from uuid import uuid4
from dotenv import load_dotenv
load_dotenv()

from semabridge.repository.orm.session_factory import db_manager
from semabridge.notifications.models import NotificationChannel
from semabridge.notifications.utils.crypto import NotificationCrypto

def main():
    crypto = NotificationCrypto()
    
    # Modern Power Automate Teams webhook URL
    webhook_url = "https://prod-12.westus.logic.azure.com:443/workflows/123/triggers/manual/paths/invoke?api-version=2016-06-01"
    config = {
        "webhook_url": webhook_url
    }
    
    encrypted_config = crypto.encrypt(json.dumps(config))
    
    try:
        with db_manager.get_session() as session:
            # Check if a Teams channel already exists, if so delete it or update it
            existing = session.query(NotificationChannel).filter(
                NotificationChannel.name == "My Teams General Channel"
            ).first()
            
            if existing:
                print("Teams channel already exists! Updating its webhook URL with correct key...")
                existing.config_json = encrypted_config
                existing.enabled = True
                session.commit()
                channel_id = existing.id
                print(f"Updated successfully! Channel ID: {channel_id}")
            else:
                print("Creating new Teams channel...")
                channel = NotificationChannel(
                    id=uuid4(),
                    name="My Teams General Channel",
                    channel_type="teams",
                    config_json=encrypted_config,
                    level_mask=63, # All levels
                    enabled=True
                )
                session.add(channel)
                session.commit()
                channel_id = channel.id
                print(f"Created successfully! Channel ID: {channel_id}")
                
    except Exception as e:
        print(f"Error creating Teams channel: {e}")

if __name__ == "__main__":
    main()
