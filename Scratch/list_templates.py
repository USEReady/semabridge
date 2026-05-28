import os
import sys
from dotenv import load_dotenv
load_dotenv()

# Force UTF-8 encoding for stdout to prevent charmap errors on Windows terminals
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

from semabridge.repository.orm.session_factory import db_manager
from semabridge.notifications.models import NotificationTemplate

def main():
    try:
        with db_manager.get_session() as session:
            templates = session.query(NotificationTemplate).all()
            print(f"Total notification templates: {len(templates)}")
            for t in templates:
                print(f"ID: {t.id}")
                print(f"Channel ID: {t.channel_id}")
                print(f"Level Mask: {t.level_mask}")
                print(f"Title Template: {t.title_template}")
                print(f"Body Template:\n{t.body_template}")
                print(f"Is Default: {t.is_default}")
                print("---" * 15)
    except Exception as e:
        print(f"Error querying database: {e}")

if __name__ == "__main__":
    main()
