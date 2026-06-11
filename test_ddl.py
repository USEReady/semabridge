import os
import sys
import logging
import asyncio
# pyrefly: ignore [missing-import]
from semabridge.api.projects_service import ProjectService
from semabridge.api.db import SessionLocal

logging.basicConfig(level=logging.DEBUG)
sys.path.insert(0, os.path.abspath('src'))

async def main():
    try:
        from semabridge.api.projects import validate_model_live
        res = await validate_model_live('b16dddf3-0cd2-4c22-9214-e6df5b91a2eb')
        print(res)
    except Exception as e:
        print(f"Error: {e}")

asyncio.run(main())
