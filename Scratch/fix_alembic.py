import os
import sys

# Add src to path
sys.path.insert(0, "src")

from dotenv import load_dotenv
load_dotenv(".env")

from sqlalchemy import create_engine, text

db_url = os.getenv("SEMABRIDGE_DATABASE_URL", "postgresql://postgres:postgres@localhost/semabridge")
print(f"Connecting to: {db_url}")

engine = create_engine(db_url)

with engine.connect() as conn:
    try:
        # Check current alembic_version
        result = conn.execute(text("SELECT * FROM alembic_version"))
        rows = result.fetchall()
        print(f"Current alembic versions: {rows}")
        
        if rows:
            # Delete all orphaned entries
            conn.execute(text("DELETE FROM alembic_version"))
            conn.commit()
            print("Cleared all alembic versions")
    except Exception as e:
        print(f"Error: {e}")
        conn.rollback()

print("Done")
