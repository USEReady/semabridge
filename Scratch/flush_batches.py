import asyncio
import os
import logging
import sys

# Setup logging to stdout
logging.basicConfig(level=logging.DEBUG, stream=sys.stdout, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from dotenv import load_dotenv
load_dotenv()
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from semabridge.notifications.workers.batch_worker import BatchingFlushWorker

async def main():
    engine = create_engine(os.getenv("SEMABRIDGE_DATABASE_URL"))
    SessionLocal = sessionmaker(bind=engine)
    db_session = SessionLocal()
    
    worker = BatchingFlushWorker(
        redis_url=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
        db_session=db_session
    )
    print("Flushing all active batches...")
    await worker.flush_all_active_batches()
    print("Done!")
    db_session.close()

if __name__ == "__main__":
    asyncio.run(main())
