import asyncio
import logging
import sys

# Configure standard logging to console
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from semabridge.notifications.utils.validators import validate_teams_webhook
from semabridge.notifications.adapters.teams_adapter import TeamsAdapter

async def main():
    print("--- 1. Testing Webhook URL Validators ---")
    urls = [
        "https://outlook.webhook.office.com/webhookb2/abc@123/IncomingWebhook/xyz",
        "https://prod-12.westus.logic.azure.com:443/workflows/123/triggers/manual/paths/invoke?api-version=2016-06-01",
        "https://prod-abc.logic.azure.com/workflows/456",
        "https://powerautomate.microsoft.com/webhooks/789",
        "https://example.com/not-teams"
    ]
    
    for url in urls:
        is_valid, err = validate_teams_webhook(url)
        print(f"URL: {url}")
        print(f"Is Valid: {is_valid} | Error: {err}")
        print("-" * 30)

    print("\n--- 2. Mocking Delivery Attempt with TeamsAdapter ---")
    adapter = TeamsAdapter({})
    payload = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [{"type": "TextBlock", "text": "🧪 SemaBridge Hardening Test"}]
                }
            }
        ],
        "plaintext_fallback": {
            "type": "message",
            "attachments": [{"contentType": "text/plain", "content": "SemaBridge Hardening Test Plaintext Fallback"}]
        }
    }
    
    # We will test using a mock/dummy/non-existent logic app URL to verify fallbacks occur properly
    channel_config = {
        "webhook_url": "https://prod-00.westus.logic.azure.com/workflows/1234567890/triggers/manual/paths/invoke?api-version=2016-06-01"
    }
    
    # Run the delivery attempt and verify that it attempts all 4 fallbacks sequentially on failure
    print("Executing send() and verifying sequential fallback attempts...")
    result = await adapter.send(payload, channel_config)
    print("Delivery result summary:")
    print(result)
    
    await adapter.close()

if __name__ == "__main__":
    asyncio.run(main())
