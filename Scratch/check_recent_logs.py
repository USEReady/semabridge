import os
import sys
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
load_dotenv()

# Force UTF-8 encoding for stdout to prevent charmap errors on Windows terminals
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

from semabridge.repository.orm.session_factory import db_manager
from semabridge.notifications.models import NotificationLog, NotificationEvent

def main():
    try:
        with db_manager.get_session() as session:
            # Query recent logs in the last 15 minutes
            fifteen_mins_ago = datetime.now(timezone.utc) - timedelta(minutes=15)
            
            # Print current time
            print(f"Current server time (UTC): {datetime.now(timezone.utc)}")
            print(f"Querying logs since: {fifteen_mins_ago}")
            
            logs = session.query(NotificationLog).filter(
                NotificationLog.created_at >= fifteen_mins_ago
            ).order_by(NotificationLog.created_at.desc()).all()
            
            print(f"Total notification logs in last 15 mins: {len(logs)}")
            print("==========================================================================")
            for log in logs:
                print(f"ID: {log.id}")
                print(f"Event ID: {log.event_id}")
                print(f"Channel ID: {log.channel_id}")
                print(f"Status: {log.status}")
                print(f"Attempt: {log.attempt}")
                print(f"Response Code: {log.response_code}")
                print(f"Response Body: {log.response_body}")
                print(f"Error Message: {log.error_message}")
                print(f"Created At: {log.created_at}")
                
                # Fetch associated event title and payload
                event = session.query(NotificationEvent).filter(NotificationEvent.id == log.event_id).first()
                if event:
                    print(f"Event Title: {event.title}")
                    print(f"Event Payload: {event.payload}")
                
                print("--------------------------------------------------------------------------")
    except Exception as e:
        print(f"Error querying database: {e}")

if __name__ == "__main__":
    main()
