import os
from dotenv import load_dotenv
load_dotenv()

from semabridge.repository.orm.session_factory import db_manager
from semabridge.notifications.models import NotificationLog

def main():
    try:
        with db_manager.get_session() as session:
            logs = session.query(NotificationLog).order_by(NotificationLog.created_at.desc()).limit(10).all()
            print(f"Total queried notification logs: {len(logs)}")
            print("==========================================================================")
            for log in logs:
                print(f"ID: {log.id}")
                print(f"Event ID: {log.event_id}")
                print(f"Channel ID: {log.channel_id}")
                print(f"Status: {log.status}")
                print(f"Attempt: {log.attempt}")
                print(f"Error Message: {log.error_message}")
                print(f"Created At: {log.created_at}")
                print("--------------------------------------------------------------------------")
    except Exception as e:
        print(f"Error querying database: {e}")

if __name__ == "__main__":
    main()
