import os
from dotenv import load_dotenv
load_dotenv()

from semabridge.repository.orm.session_factory import db_manager
from semabridge.notifications.models import NotificationChannel

def main():
    with db_manager.get_session() as session:
        for c in session.query(NotificationChannel).all():
            print(f"Name: {c.name}")
            print(f"  Status: {c.status}")
            print(f"  Enabled: {c.enabled}")
            print(f"  Level Mask: {c.level_mask}")
            print(f"  Channel Type: {c.channel_type}")
            print("---")

if __name__ == "__main__":
    main()
