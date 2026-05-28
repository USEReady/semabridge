import asyncio
import sys
from dotenv import load_dotenv
load_dotenv()

# Force UTF-8 encoding for Windows terminals
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')

from semabridge.notifications.adapters.email_adapter import EmailAdapter

async def main():
    print("1. Creating dummy SMTP configuration...")
    # This config will represent a real operational SMTP configuration
    config = {
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_username": "alerts@gmail.com",
        "smtp_password": "google-app-password",
        "from_email": "alerts@gmail.com",
        "to_emails": "recipient1@company.com, recipient2@company.com",
        "tls_enabled": True
    }
    
    print(f"SMTP Host: {config['smtp_host']}")
    print(f"SMTP Port: {config['smtp_port']}")
    print(f"SMTP Username: {config['smtp_username']}")
    print(f"From Email: {config['from_email']}")
    print(f"To Emails: {config['to_emails']}")
    print(f"TLS Enabled: {config['tls_enabled']}")
    print("-" * 50)
    
    print("2. Instantiating EmailAdapter...")
    adapter = EmailAdapter(config)
    
    print("3. Validating SMTP configuration...")
    is_valid, err = await adapter.validate_config(config)
    print(f"Validation successful? {is_valid}")
    if not is_valid:
        print(f"Validation error: {err}")
        return
        
    print("-" * 50)
    print("4. Attempting to execute test_connection() with Gmail (expects Auth/Connection failure unless real App Password is set)...")
    # Since we are using dummy values, it should catch the SMTP connection or login failure
    # and return success=False along with the exact SMTP response, without crashing!
    success, error_msg = await adapter.test_connection(config)
    
    print(f"Test Connection Success: {success}")
    print(f"SMTP Error/Details returned: {error_msg}")
    print("=====================================================")
    print("Verify that no passwords are leaked in the error message or logs above.")
    print("=====================================================")

if __name__ == "__main__":
    asyncio.run(main())
