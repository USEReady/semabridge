import asyncio
from semabridge.api.services.project_runs_impl import clear_job_runs_compat

async def test():
    res = await clear_job_runs_compat()
    print(res)

if __name__ == '__main__':
    asyncio.run(test())
