import os
import sys

# Load env
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, inspect

db_url = os.environ.get("DATABASE_URL")
if not db_url:
    print("No DATABASE_URL")
    sys.exit(1)

engine = create_engine(db_url)
inspector = inspect(engine)

for constraint in inspector.get_unique_constraints("accounts"):
    print(constraint)
