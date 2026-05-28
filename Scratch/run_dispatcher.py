import os
import asyncio
import logging
from dotenv import load_dotenv
load_dotenv()

# Setup logging to see the output clearly
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

from semabridge.repository.orm.session_factory import db_manager
from semabridge.notifications.workers.dispatcher import DispatcherWorker

async def main():
    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    print(f"Connecting to database: {db_manager.get_engine().url}")
    print(f"Connecting to Redis: {redis_url}")
    
    with db_manager.get_session() as session:
        dispatcher = DispatcherWorker(redis_url, session, max_workers=2)
        print("Starting DispatcherWorker...")
        await dispatcher.start()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Dispatcher worker stopped by user.")
