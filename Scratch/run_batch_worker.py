import asyncio
import os
import logging
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from semabridge.notifications.workers.batch_worker import BatchingFlushWorker

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

async def main():
    database_url = os.getenv("SEMABRIDGE_DATABASE_URL")
    if not database_url:
        raise RuntimeError("SEMABRIDGE_DATABASE_URL environment variable missing")

    engine = create_engine(database_url)
    SessionLocal = sessionmaker(bind=engine)
    db_session = SessionLocal()

    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

    worker = BatchingFlushWorker(
        redis_url=redis_url,
        db_session=db_session,
        poll_interval_sec=5
    )

    print("Starting Batching Flush Worker...")
    try:
        await worker.start()
    except KeyboardInterrupt:
        await worker.stop()
    finally:
        db_session.close()

if __name__ == "__main__":
    asyncio.run(main())
