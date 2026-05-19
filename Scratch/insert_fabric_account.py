import os
import sys

sys.path.insert(0, "src")

from dotenv import load_dotenv
load_dotenv(".env")

from sqlalchemy import create_engine, text

db_url = os.getenv("SEMABRIDGE_DATABASE_URL", "postgresql://postgres:postgres@localhost/semabridge")

engine = create_engine(db_url)
account_id = 'd122e41d-d980-4abf-b1e8-5b0d3c7b13be'

with engine.begin() as conn:
    try:
        conn.execute(
            text("""
                INSERT INTO accounts (id, connector_type, tag, status, is_default, auth_type) 
                VALUES (:id, 'FABRIC', 'Mock Fabric Target Account', 'active', true, 'oauth2')
            """), 
            {
                "id": account_id
            }
        )
        print("Done")
    except Exception as e:
        print("Error:", e)
