import os
import sys
from uuid import UUID
from dotenv import load_dotenv
load_dotenv()

# Set UTF-8 encoding for Windows terminals
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

from semabridge.repository.orm.session_factory import db_manager
from semabridge.notifications.models import NotificationTemplate

def main():
    template_id = "f060ba34-a924-4b8b-ba6e-b71b296fcd7b"
    new_title = "✅ Sync Completed Successfully"
    new_body = """Synchronization completed successfully.

━━━━━━━━━━━━━━
📌 Project: {{ project_id }}
🔄 Mode: {{ payload.sync_mode }}

⬅️ Source: {{ payload.source_platform }}
➡️ Target: {{ payload.target_platform }}

📊 Datasets: {{ payload.datasets }}
📈 Metrics: {{ payload.metrics }}

🆔 Run ID: {{ sync_job_id }}"""

    try:
        with db_manager.get_session() as session:
            template = session.query(NotificationTemplate).filter(
                NotificationTemplate.id == UUID(template_id)
            ).first()
            
            if not template:
                print(f"Error: Template with ID {template_id} not found in database.")
                return
            
            print("Current Template State:")
            print(f"Title: {template.title_template}")
            print(f"Body:\n{template.body_template}")
            print("-" * 40)
            
            # Update fields
            template.title_template = new_title
            template.body_template = new_body
            
            session.commit()
            print("Successfully updated database template!")
            print("-" * 40)
            
            # Fetch again to verify
            updated_template = session.query(NotificationTemplate).filter(
                NotificationTemplate.id == UUID(template_id)
            ).first()
            print("Updated Template State:")
            print(f"Title: {updated_template.title_template}")
            print(f"Body:\n{updated_template.body_template}")
            
    except Exception as e:
        print(f"Database update failed: {e}")

if __name__ == "__main__":
    main()
