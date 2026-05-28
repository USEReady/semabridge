import json
import asyncio
from semabridge.repository.orm.session_factory import db_manager
from semabridge.notifications.models import NotificationChannel
from semabridge.notifications.utils.crypto import NotificationCrypto
from semabridge.notifications.adapters.teams_adapter import TeamsAdapter

async def main():
    crypto = NotificationCrypto()
    
    try:
        with db_manager.get_session() as session:
            channel = session.query(NotificationChannel).filter(
                NotificationChannel.name == "My Teams General Channel"
            ).first()
            
            if not channel:
                print("Error: My Teams General Channel not found.")
                return
                
            print(f"Loaded channel: {channel.name} ({channel.id})")
            decrypted_config = crypto.decrypt(channel.config_json)
            config_json = json.loads(decrypted_config)
            
            adapter = TeamsAdapter(config_json)
            print("Invoking test_connection()...")
            success, err = await adapter.test_connection(config_json)
            print(f"Test Connection Success: {success} | Error: {err}")
            
            await adapter.close()
            
    except Exception as e:
        print(f"Error testing button flow: {e}")

if __name__ == "__main__":
    asyncio.run(main())
