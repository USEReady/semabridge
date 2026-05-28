import os
from dotenv import load_dotenv
load_dotenv()

from semabridge.repository.orm.session_factory import db_manager
from semabridge.notifications.models import NotificationChannel

def main():
    try:
        with db_manager.get_session() as session:
            channels = session.query(NotificationChannel).all()
            print(f"Total notification channels: {len(channels)}")
            for channel in channels:
                print(f"ID: {channel.id}")
                print(f"Name: {channel.name}")
                print(f"Type: {channel.channel_type}")
                print(f"Enabled: {channel.enabled}")
                print("---")
    except Exception as e:
        print(f"Error querying database: {e}")

if __name__ == "__main__":
    main()
