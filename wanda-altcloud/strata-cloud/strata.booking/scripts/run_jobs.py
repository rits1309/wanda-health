"""Run every due background job once (`inv jobs-run`)."""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from strata_booking.core.config import settings
from strata_booking.services import jobs


async def main() -> None:
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        results = await jobs.run_all(session)
    await engine.dispose()
    print(results)


if __name__ == "__main__":
    asyncio.run(main())
