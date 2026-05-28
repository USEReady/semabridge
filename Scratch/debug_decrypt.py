import json
from semabridge.repository.orm.session_factory import db_manager
from semabridge.notifications.models import NotificationChannel
from semabridge.notifications.utils.crypto import NotificationCrypto

def main():
    crypto = NotificationCrypto()
    print("Fernet Key used by script:", crypto.fernet)
    
    try:
        with db_manager.get_session() as session:
            channel = session.query(NotificationChannel).filter(
                NotificationChannel.name == "My Teams General Channel"
            ).first()
            
            if not channel:
                print("Error: My Teams General Channel not found.")
                return
                
            print("Channel Name:", channel.name)
            print("Encrypted config_json in DB:", channel.config_json)
            
            # Try decrypting
            try:
                decrypted = crypto.decrypt(channel.config_json)
                print("Decrypted raw string:", decrypted)
                parsed = json.loads(decrypted)
                print("Parsed successfully:", parsed)
            except Exception as e:
                print("Failed to decrypt or parse:", e)
                
    except Exception as e:
        print("DB error:", e)

if __name__ == "__main__":
    main()
