import os
import json
from sqlalchemy import create_engine, text
from dotenv import load_dotenv
load_dotenv()

def main():
    engine = create_engine(os.getenv('SEMABRIDGE_DATABASE_URL'))
    
    TEAMS_ID = 'db2573cf-fa91-42f1-a7fb-49d9db4ca2eb'
    FANOUT_RULES = [
        'Sync Success Alerts',
        'Sync Failure Alerts',
    ]
    
    with engine.connect() as conn:
        print("=== Current routing rules before update ===")
        rows = conn.execute(text("SELECT id, name, channel_ids FROM notification_routing_rules ORDER BY priority")).fetchall()
        for r in rows:
            d = dict(r._mapping)
            print(f"  [{d['name']}] channel_ids={d['channel_ids']}")
            
        print("\n=== Adding Teams to Success and Failure rules ===")
        for rule_name in FANOUT_RULES:
            result = conn.execute(text(
                "SELECT id, channel_ids FROM notification_routing_rules WHERE name = :name"
            ), {"name": rule_name}).fetchone()
            if result:
                current_ids = result.channel_ids if isinstance(result.channel_ids, list) else json.loads(result.channel_ids or '[]')
                if TEAMS_ID not in current_ids:
                    new_ids = current_ids + [TEAMS_ID]
                    conn.execute(text(
                        "UPDATE notification_routing_rules SET channel_ids = :ids WHERE id = :id"
                    ), {"ids": json.dumps(new_ids), "id": str(result.id)})
                    print(f"  Updated '{rule_name}': {current_ids} -> {new_ids}")
                else:
                    print(f"  '{rule_name}' already has Teams channel")
            else:
                print(f"  WARNING: Rule '{rule_name}' not found!")
                
        conn.commit()
        
        print("\n=== Final updated routing rules ===")
        rows = conn.execute(text("SELECT id, name, channel_ids FROM notification_routing_rules ORDER BY priority")).fetchall()
        for r in rows:
            d = dict(r._mapping)
            print(f"  [{d['name']}] channel_ids={d['channel_ids']}")

if __name__ == "__main__":
    main()
