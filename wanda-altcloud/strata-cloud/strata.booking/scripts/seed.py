"""Deterministic local seed (`inv seed`) — delegates to the canonical profile.

The dataset is defined ONCE, in ``strata_core.fixtures``: the ``booking-acceptance``
profile converges the demo cast (under booking's historical ids — ``c-1``/``m-1``/…)
plus the booking walkthrough data (programmes, assignments via the kernel's
``role_id``, policies, patterns, example slots/appointments). Idempotent: safe to
re-run; rows are converged/merged, never duplicated. Requires a migrated database
(``cd ../strata.core && inv migrate``).
"""

import asyncio

from sqlalchemy.ext.asyncio import create_async_engine
from strata_core.fixtures import load_profile

from strata_booking.core.config import settings


async def main() -> None:
    engine = create_async_engine(settings.database_url)
    try:
        report = await load_profile(engine, "booking-acceptance")
    finally:
        await engine.dispose()
    print(f"seeded: profile {report.profile!r}, {report.people} people.")


if __name__ == "__main__":
    asyncio.run(main())
