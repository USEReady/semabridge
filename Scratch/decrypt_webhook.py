import os
import json
from dotenv import load_dotenv
load_dotenv()

from semabridge.repository.orm.session_factory import db_manager
from semabridge.notifications.models import NotificationChannel
from semabridge.notifications.utils.crypto import NotificationCrypto

def main():
    try:
        with db_manager.get_session() as session:
            channel = session.query(NotificationChannel).filter(
                NotificationChannel.name == "My slack workspace"
            ).first()
            if not channel:
                print("Could not find channel 'My slack workspace'")
                return
            
            print(f"Channel Name: {channel.name}")
            print(f"Channel Type: {channel.channel_type}")
            print(f"Enabled: {channel.enabled}")
            
            # Decrypt config_json
            crypto = NotificationCrypto()
            config_json_raw = channel.config_json
            if isinstance(config_json_raw, str):
                decrypted_str = crypto.decrypt(config_json_raw)
                config_json = json.loads(decrypted_str)
                print("Decrypted config successful!")
                print(f"Webhook URL: {config_json.get('webhook_url')}")
            else:
                print(f"config_json is not a string, it is: {type(config_json_raw)}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    main()
