import sys
import os
import asyncio
sys.path.insert(0, os.path.abspath('src'))

from semabridge.repository.model_repository import ModelRepository
from semabridge.api.repo_router import _get_all_models, get_snapshot_tree

async def main():
    repo = ModelRepository()
    # Ensure it uses the existing DB manager
    
    print("\n--- Testing _get_all_models ---")
    try:
        models = _get_all_models(repo)
        print("Models retrieved:", len(models))
    except Exception as e:
        print("Error in _get_all_models:", e)

    print("\n--- Testing get_snapshot_tree ---")
    try:
        tree = await get_snapshot_tree(repo)
        if "root" in tree:
            print("Tree built successfully! Children nodes count:", len(tree["root"].get("children", [])))
        else:
            print("Tree generated unexpectedly:", tree)
    except Exception as e:
        print("Error in get_snapshot_tree:", e)

if __name__ == "__main__":
    asyncio.run(main())
