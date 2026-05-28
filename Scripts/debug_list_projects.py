import asyncio
from semabridge.api.services import project_projects_impl as ppi

async def main():
    rows = await ppi.list_projects_compat()
    for r in rows:
        print(r.get('project_id'), '->', r.get('name'), ' (semantic_models=', r.get('semantic_models'), ')')

asyncio.run(main())
