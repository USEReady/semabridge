import os
import sys

sys.path.insert(0, "src")

from dotenv import load_dotenv
load_dotenv(".env")

from sqlalchemy import create_engine, text

db_url = os.getenv("SEMABRIDGE_DATABASE_URL", "postgresql://postgres:postgres@localhost/semabridge")
print(f"Connecting to: {db_url}")

engine = create_engine(db_url)

with engine.connect() as conn:
    res = conn.execute(text("SELECT column_name, character_maximum_length FROM information_schema.columns WHERE table_name = 'projects' AND column_name = 'project_id'"))
    for row in res:
        print(f"projects.project_id max length in DB: {row[1]}")
