import os
import sys
import json
import asyncio
from uuid import UUID
from dotenv import load_dotenv
load_dotenv()

# Force UTF-8 encoding for Windows terminals
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

from semabridge.repository.orm.session_factory import db_manager
from semabridge.notifications.models import NotificationChannel, NotificationEvent, NotificationTemplate as DBTemplate
from semabridge.notifications.services.template_service import TemplateService, NotificationTemplate
from semabridge.notifications.formatters.slack_formatter import SlackFormatter
from semabridge.notifications.adapters.slack_adapter import SlackAdapter
from semabridge.notifications.utils.crypto import NotificationCrypto

async def main():
    try:
        print("1. Fetching channel configuration from PostgreSQL...")
        with db_manager.get_session() as session:
            channel = session.query(NotificationChannel).filter(
                NotificationChannel.name == "My slack workspace"
            ).first()
            if not channel:
                print("Error: Slack channel config not found in DB.")
                return
            
            channel_id = str(channel.id)
            crypto = NotificationCrypto()
            decrypted_str = crypto.decrypt(channel.config_json)
            config_json = json.loads(decrypted_str)
            print("Successfully loaded Slack credentials.")

            print("2. Fetching success template from PostgreSQL...")
            db_template = session.query(DBTemplate).filter(
                DBTemplate.id == UUID("f060ba34-a924-4b8b-ba6e-b71b296fcd7b")
            ).first()
            if not db_template:
                print("Error: Default success template not found in DB.")
                return
            
            # Load template using our domain template model
            template = NotificationTemplate(
                id=str(db_template.id),
                channel_id=str(db_template.channel_id),
                level_mask=db_template.level_mask,
                title_template=db_template.title_template,
                body_template=db_template.body_template,
                is_default=db_template.is_default
            )
            print("Successfully loaded template:")
            print(f"Title template: {template.title_template}")
            print(f"Body template:\n{template.body_template}")
            print("-" * 50)

        # 3. Construct the Event with Enriched Metadata
        print("3. Building simulated sync completed event with rich payload metadata...")
        payload = {
            "duration": "42.50s",
            "mode": "copy",
            "sync_mode": "copy",
            "run_id": "run-debug-notif-success-e2e-abc-123",
            "project_id": "competative-sync-notif",
            "source_platform": "Microsoft Fabric",
            "target_platform": "Snowflake",
            "dataset_count": 4,
            "metric_count": 12,
            "datasets": 4,  # compatibility
            "metrics": 12,    # compatibility
        }
        
        event = NotificationEvent(
            level=16,  # INFO level for success completed
            title="Sync Completed Successfully",
            message="Synchronization completed successfully in 42.50s",
            source="sync_engine",
            project_id="competative-sync-notif",
            sync_job_id="run-debug-notif-success-e2e-abc-123",
            payload=payload,
        )

        # 4. Render Template
        print("4. Rendering template through Sandboxed Jinja2 environment...")
        template_svc = TemplateService()
        rendered_title, rendered_body = template_svc.render(template, event)
        print("Rendered Title:")
        print(rendered_title)
        print("Rendered Body:")
        print(rendered_body)
        print("-" * 50)

        from dataclasses import replace
        event_for_delivery = replace(
            event,
            title=rendered_title,
            message=rendered_body
        )

        # 5. Format using SlackFormatter
        print("5. Formatting event using SlackFormatter (Block Kit)...")
        formatter = SlackFormatter()
        formatted_payload = formatter.format(event_for_delivery, config_json)
        print(f"Formatted payload keys: {list(formatted_payload.keys())}")
        print("Blocks structure:")
        print(json.dumps(formatted_payload.get("blocks"), indent=2))
        print("-" * 50)

        # 6. Deliver using SlackAdapter
        print("6. Dispatching delivery via SlackAdapter...")
        adapter = SlackAdapter(config_json)
        try:
            result = await adapter.send(formatted_payload, config_json)
            print("=====================================================")
            print("E2E Verification Delivery Result:")
            print(json.dumps(result, indent=2))
            print("=====================================================")
        finally:
            await adapter.close()

    except Exception as e:
        print(f"E2E test exception: {e}")

if __name__ == "__main__":
    asyncio.run(main())
