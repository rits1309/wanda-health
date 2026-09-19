"""Named profiles and the loader that converges a database to one.

Profiles compose: ``demo`` is ``minimal`` plus the dev cast;
``booking-acceptance`` is ``demo`` plus the booking-only extras and the
booking walkthrough dataset (programmes, assignments, policies, patterns,
example slots/appointments); ``readings-demo`` is ``demo`` plus the
device registrations for the readings walkthrough (Connect);
``clinical-demo`` is ``demo`` plus a small clinical slice across the
health-profile tables. Loading requires a migrated database (the
canonical migrations provide the schema and the role catalogue); profiles
only add data.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from strata_core.domains.kernel import (
    LEGACY_SUMMIT_DJANGO_USER_ID,
    LEGACY_SUMMIT_PATIENT_ID,
    ROLE_NAMES,
    Role,
)
from strata_core.fixtures.booking_data import (
    converge_migrated_member_assignments,
    load_booking_data,
)
from strata_core.fixtures.cast import BOOKING_EXTRAS, DEMO_CAST, CastMember
from strata_core.fixtures.clinical_data import load_clinical_data
from strata_core.fixtures.factories import converge_roles, create_profile, register_device
from strata_core.fixtures.readings_data import load_readings_data

# The registration instant for the fixture's synthetic legacy mappings — fixed
# for a deterministic dataset, not "now".
_LEGACY_SEED_AT = datetime(2025, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class ProfileReport:
    profile: str
    people: int


async def _check_catalogue(engine: AsyncEngine) -> None:
    """The schema precondition: migrated, role catalogue present."""
    async with async_sessionmaker(engine)() as session:
        names = (await session.execute(select(Role.name))).scalars().all()
    missing = set(ROLE_NAMES) - set(names)
    if missing:
        raise RuntimeError(
            f"role catalogue incomplete (missing {sorted(missing)}) — run `inv migrate` first"
        )


async def _load_minimal(engine: AsyncEngine, now: datetime | None = None) -> ProfileReport:
    """The kernel skeleton: migrated schema + role catalogue, no data."""
    await _check_catalogue(engine)
    return ProfileReport("minimal", people=0)


async def _converge_cast(engine: AsyncEngine, cast: tuple[CastMember, ...]) -> dict[str, str]:
    """Converge the cast; return historical cast id → ACTUAL profile id.

    The two differ when a cast email was first provisioned outside the fixtures
    (real sign-up under a generated id) — the converge keeps that row's id, and
    downstream datasets must reference people through this map, never literally.
    """
    ids: dict[str, str] = {}
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        for member in cast:
            profile = await create_profile(
                session,
                email=member.email,
                first_name=member.first_name,
                last_name=member.last_name,
                display_name=member.display_name,
                cognito_sub=member.placeholder_sub,
                timezone=member.timezone,
                languages=member.languages,
                preferred_language=member.preferred_language,
                profile_id=member.profile_id,
            )
            await converge_roles(session, profile, member.roles)
            # Post-migrated stand-ins carry the Legacy Summit mappings an import
            # leaves behind — converged with the cast so every profile
            # that includes them agrees. Idempotent via register_device's
            # convergence + deterministic registration ids.
            for type_name, external_id, tag in (
                (LEGACY_SUMMIT_DJANGO_USER_ID, member.legacy_django_id, "django"),
                (LEGACY_SUMMIT_PATIENT_ID, member.legacy_patient_id, "patient"),
            ):
                if external_id is not None:
                    await register_device(
                        session,
                        user_id=profile.id,
                        type_name=type_name,
                        external_id=external_id,
                        registered_at=_LEGACY_SEED_AT,
                        registration_id=f"legacy-{tag}-{member.profile_id}",
                    )
            ids[member.profile_id] = profile.id
        await session.commit()
    return ids


async def _load_demo(engine: AsyncEngine, now: datetime | None = None) -> ProfileReport:
    """The dev cast, converged exactly (people, roles)."""
    await _check_catalogue(engine)
    ids = await _converge_cast(engine, DEMO_CAST)
    return ProfileReport("demo", people=len(ids))


async def _load_booking_acceptance(
    engine: AsyncEngine, now: datetime | None = None
) -> ProfileReport:
    """The demo cast + booking extras + the booking walkthrough dataset.

    Also converges migrated members into the walkthrough: any
    Member-role profile holding a Legacy Summit Django User ID mapping is
    assigned into p-1, so imported people appear in scoped listings on reseed.
    """
    await _check_catalogue(engine)
    ids = await _converge_cast(engine, DEMO_CAST + BOOKING_EXTRAS)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await load_booking_data(session, people=ids, now=now)
        await converge_migrated_member_assignments(session)
        await session.commit()
    return ProfileReport("booking-acceptance", people=len(ids))


async def _load_readings_demo(engine: AsyncEngine, now: datetime | None = None) -> ProfileReport:
    """The demo cast + the readings walkthrough device registrations (Connect)."""
    await _check_catalogue(engine)
    ids = await _converge_cast(engine, DEMO_CAST)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await load_readings_data(session, people=ids)
        await session.commit()
    return ProfileReport("readings-demo", people=len(ids))


async def _load_clinical_demo(engine: AsyncEngine, now: datetime | None = None) -> ProfileReport:
    """The demo cast + the clinical health-profile slice."""
    await _check_catalogue(engine)
    ids = await _converge_cast(engine, DEMO_CAST)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await load_clinical_data(session, people=ids)
        await session.commit()
    return ProfileReport("clinical-demo", people=len(ids))


PROFILES: dict[str, Callable[..., Awaitable[ProfileReport]]] = {
    "minimal": _load_minimal,
    "demo": _load_demo,
    "booking-acceptance": _load_booking_acceptance,
    "readings-demo": _load_readings_demo,
    "clinical-demo": _load_clinical_demo,
}


async def load_profile(
    engine: AsyncEngine, name: str, *, now: datetime | None = None
) -> ProfileReport:
    """Converge the database to ``name``; ``people`` in the report counts the cast
    converged (not every row in the table). ``now`` anchors date-relative datasets —
    pass the pinned clock when loading under a frozen time seam."""
    try:
        loader = PROFILES[name]
    except KeyError:
        raise ValueError(f"unknown profile {name!r} — one of {sorted(PROFILES)}") from None
    return await loader(engine, now)


def main(argv: list[str] | None = None) -> None:
    """CLI: ``python -m strata_core.fixtures --profile demo`` (used by ``inv seed``)."""
    import argparse

    from strata_core.settings import Settings

    parser = argparse.ArgumentParser(description="Load a named fixture profile.")
    parser.add_argument("--profile", default="demo", choices=sorted(PROFILES))
    args = parser.parse_args(argv)

    settings = Settings(_env_file=".env")

    async def run() -> ProfileReport:
        engine = create_async_engine(settings.database_url)
        try:
            return await load_profile(engine, args.profile)
        finally:
            await engine.dispose()

    report = asyncio.run(run())
    print(f"profile {report.profile!r} loaded: {report.people} people")
