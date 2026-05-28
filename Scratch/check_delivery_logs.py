"""
Verify email was actually delivered by checking delivery logs.
"""
import os
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, text
engine = create_engine(os.getenv('SEMABRIDGE_DATABASE_URL'))
with engine.connect() as conn:
    print('=== DELIVERY LOGS (last 20) ===')
    rows = conn.execute(text('''
        SELECT nl.id, nl.event_id, nc.name, nc.channel_type, nl.status, nl.response_code, nl.error_message, nl.created_at
        FROM notification_logs nl
        JOIN notification_channels nc ON nl.channel_id = nc.id
        ORDER BY nl.created_at DESC
        LIMIT 20
    ''')).fetchall()
    for r in rows:
        d = dict(r._mapping)
        err = d.get('error_message') or ""
        print(f"  {d['created_at']} | {d['channel_type']:8} | {d['name']:25} | {d['status']:15} | rc={d['response_code']} | {err[:60]}")
