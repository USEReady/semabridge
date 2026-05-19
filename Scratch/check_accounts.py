import os
import sys

sys.path.insert(0, "src")

from dotenv import load_dotenv
load_dotenv(".env")

from sqlalchemy import create_engine, text

db_url = os.getenv("SEMABRIDGE_DATABASE_URL", "postgresql://postgres:postgres@localhost/semabridge")

engine = create_engine(db_url)

with engine.connect() as conn:
    res = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'accounts'"))
    cols = [row[0] for row in res]
    print(cols)
