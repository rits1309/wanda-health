"""The booking walkthrough dataset — ported from booking's Phase 1 seed.

Two programmes prove programme scoping; three coaches cover the
language-matching and same-time-reassignment scenarios; a coach-level
cancellation policy proves override precedence; a few explicit slots (+3
bookings) give the calendar content before slot generation materialises
pattern slots. People are the demo cast (plus the booking-only extras) under
their deterministic ids; membership *kind* is now the kernel assignment's
``role_id``, mapped from booking's old ``user_kind`` strings.

Rows are merged by fixed ids, so loading is idempotent. People are referenced
through the ``people`` map (historical cast id → actual profile id) built by the
cast converge — so a cast member first provisioned under a generated id (real
sign-up before any seed) is honoured instead of FK-violating. Slot/appointment
placement derives entirely from ``now`` (injectable — pass the pinned clock when
loading under a frozen time seam; defaults to wall clock): the example slots
scatter over the weekdays of the next seven days at times drawn from an RNG
seeded with ``now``, so every real reseed reshuffles them (always future,
always onto weekdays Summit's Mon–Fri calendar can show) while a pinned clock
reproduces the same rows — determinism is anchored on the injected clock.
"""

import random
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from strata_core.domains.booking import (
    Appointment,
    AvailabilityPattern,
    CancellationPolicy,
    Slot,
    UnavailabilityPattern,
)
from strata_core.domains.kernel import (
    EXTERNAL_ID_TYPE_IDS,
    LEGACY_SUMMIT_DJANGO_USER_ID,
    ROLE_BY_KIND,
    ROLE_IDS,
    Programme,
    ProgrammeAssignment,
    UserExternalId,
    UserRole,
)

# The example slots draw from a 20-minute grid over the coach's working day —
# 13:00–20:40 UTC is 9:00am–4:40pm America/New_York (EDT), matching c-1's pattern hours.
SLOT_GRID_UTC_HOURS = range(13, 21)
SLOT_GRID_MINUTES = (0, 20, 40)

#: (programme, old user_kind, profile id) — the kind string maps onto the catalogue role.
ASSIGNMENTS: tuple[tuple[str, str, str], ...] = (
    ("p-1", "coach", "c-1"),
    ("p-1", "coach", "c-2"),
    ("p-1", "coach", "c-3"),
    ("p-2", "coach", "c-2"),
    ("p-1", "member", "m-1"),
    ("p-1", "member", "m-2"),
    ("p-1", "member", "m-3"),
    ("p-1", "member", "m-4"),
    ("p-2", "member", "m-5"),
    ("p-1", "admin", "a-1"),
    ("p-2", "admin", "a-1"),
    # Dana administers p-1 ONLY (unlike all-programmes Alex): the multi-role
    # member also proves SCOPED admin — p-1's coaches, never p-2's data.
    ("p-1", "admin", "c-2"),
)


def _programmes() -> list[Programme]:
    return [
        Programme(id="p-1", name="GLP-1 Coaching"),
        Programme(id="p-2", name="Hypertension Support"),
    ]


def _assignments(people: dict[str, str]) -> list[ProgrammeAssignment]:
    return [
        ProgrammeAssignment(
            id=f"pa-{programme}-{kind}-{user}",
            programme_id=programme,
            user_id=people[user],
            role_id=ROLE_IDS[ROLE_BY_KIND[kind]],
        )
        for programme, kind, user in ASSIGNMENTS
    ]


def _policies(people: dict[str, str]) -> list[CancellationPolicy]:
    return [
        CancellationPolicy(
            id="pol-p1-v1",
            scope="programme",
            programme_id="p-1",
            cancellation_window_hours=24,
            cancellation_allowed_by="both",
            version=1,
        ),
        CancellationPolicy(
            id="pol-p2-v1",
            scope="programme",
            programme_id="p-2",
            cancellation_window_hours=24,
            cancellation_allowed_by="both",
            version=1,
        ),
        # Coach override proves precedence over the programme default.
        CancellationPolicy(
            id="pol-c2-v1",
            scope="coach",
            programme_id="p-1",
            coach_id=people["c-2"],
            cancellation_window_hours=48,
            cancellation_allowed_by="coach",
            version=1,
        ),
    ]


def _patterns(today: date, people: dict[str, str]) -> list[Any]:
    active_to = today + timedelta(weeks=12)
    return [
        AvailabilityPattern(
            id="ap-c1",
            coach_id=people["c-1"],
            days_of_week=[0, 2, 4],  # Mon/Wed/Fri
            start_time_local=time(9, 0),
            end_time_local=time(15, 0),
            slot_duration_minutes=20,
            timezone="America/New_York",
            active_from=today,
            active_to=active_to,
        ),
        AvailabilityPattern(
            id="ap-c2",
            coach_id=people["c-2"],
            days_of_week=[1, 3],  # Tue/Thu
            start_time_local=time(8, 0),
            end_time_local=time(12, 0),
            slot_duration_minutes=30,
            timezone="America/Chicago",
            active_from=today,
            active_to=active_to,
        ),
        AvailabilityPattern(
            id="ap-c3",
            coach_id=people["c-3"],
            days_of_week=[0, 1, 2, 3, 4],  # weekdays
            start_time_local=time(9, 0),
            end_time_local=time(17, 0),
            slot_duration_minutes=60,
            timezone="America/Los_Angeles",
            active_from=today,
            active_to=active_to,
        ),
        UnavailabilityPattern(
            id="up-c3-lunch",
            coach_id=people["c-3"],
            days_of_week=[0, 1, 2, 3, 4],
            start_time_local=time(12, 0),
            end_time_local=time(13, 0),
            timezone="America/Los_Angeles",
            active_from=today,
            active_to=active_to,
            created_by=people["c-3"],
        ),
    ]


SEED_SLOT_IDS = tuple(f"seed-slot-c1-{i}" for i in range(6))


async def _place_example_slots(
    session: AsyncSession, now: datetime, coach_id: str, rng: random.Random
) -> dict[str, datetime]:
    """Pick a start for each seed slot: random future weekday grid positions.

    The candidates span the weekdays of the next seven days — weekday-only, so
    Summit's Mon–Fri calendar always shows them (a blind "tomorrow" seeded on a
    Friday used to land them all on Saturday, invisible), and strictly in the
    future, preserving the old seed's guarantee that the booked examples are
    actionable (booking rejects past slots). ``rng`` is seeded from the load's
    ``now``: every reseed reshuffles the placement, a pinned clock reproduces it.

    Placement is convergence-safe against the (coach, start, end) unique key:
    positions held by slots this converge does not own (e.g. materialised
    pattern slots) are excluded, and a seed slot already sitting on a sampled
    position keeps it — so moved slots only ever land on genuinely free
    positions (no mid-flush collisions) and a pinned-clock reload is a no-op.
    """
    days = [
        day
        for day_offset in range(1, 8)  # tomorrow .. 7 days out: always exactly 5 weekdays
        if (day := now.date() + timedelta(days=day_offset)).weekday() < 5
    ]
    grid = [
        datetime.combine(day, time(hour, minute), tzinfo=UTC)
        for day in days
        for hour in SLOT_GRID_UTC_HOURS
        for minute in SLOT_GRID_MINUTES
    ]
    existing: dict[str, datetime] = dict(
        (await session.execute(select(Slot.id, Slot.start_utc).where(Slot.coach_id == coach_id)))
        .tuples()
        .all()
    )
    occupied_by_others = {
        start for slot_id, start in existing.items() if slot_id not in SEED_SLOT_IDS
    }
    candidates = [start for start in grid if start not in occupied_by_others]
    starts = set(rng.sample(candidates, len(SEED_SLOT_IDS)))
    kept = {
        slot_id: existing[slot_id] for slot_id in SEED_SLOT_IDS if existing.get(slot_id) in starts
    }
    fresh = sorted(starts - set(kept.values()))
    movable = [slot_id for slot_id in SEED_SLOT_IDS if slot_id not in kept]
    return kept | dict(zip(movable, fresh, strict=True))


def _example_slots_and_appointments(
    slot_starts: dict[str, datetime], people: dict[str, str]
) -> list[Any]:
    """A few explicit slots (+3 bookings) so the calendar has content before generation."""
    rows: list[Any] = []
    for i, slot_id in enumerate(SEED_SLOT_IDS):
        start = slot_starts[slot_id]
        rows.append(
            Slot(
                id=slot_id,
                coach_id=people["c-1"],
                start_utc=start,
                end_utc=start + timedelta(minutes=20),
                duration_minutes=20,
                status="booked" if i < 3 else "available",
            )
        )
    for i, member in enumerate(["m-1", "m-3", "m-4"]):
        rows.append(
            Appointment(
                id=f"seed-appt-{i}",
                slot_id=f"seed-slot-c1-{i}",
                coach_id=people["c-1"],
                member_id=people[member],
                programme_id="p-1",
                created_by="member",
                acting_user_id=people[member],
                booking_language_matched=member == "m-1",
                booking_preferred_language="en" if member == "m-1" else None,
                idempotency_key=f"{people[member]}:seed-slot-c1-{i}",
            )
        )
    return rows


async def load_booking_data(
    session: AsyncSession, people: dict[str, str], now: datetime | None = None
) -> None:
    """Merge the walkthrough rows (idempotent by fixed ids); flushes, does not commit.

    ``people`` maps the cast's historical ids to the actual converged profile ids;
    ``now`` anchors the rolling calendar AND seeds the slot-placement RNG (pass
    the pinned clock under a frozen seam for reproducible rows).
    """
    anchor = now or datetime.now(tz=UTC)
    today = anchor.date()
    # Fixture placement, not crypto (ruff S311 / bandit B311).
    rng = random.Random(int(anchor.timestamp()))  # noqa: S311  # nosec B311
    slot_starts = await _place_example_slots(session, anchor, people["c-1"], rng)
    for row in [
        *_programmes(),
        *_assignments(people),
        *_policies(people),
        *_patterns(today, people),
        *_example_slots_and_appointments(slot_starts, people),
    ]:
        await session.merge(row)
    await session.flush()


async def converge_migrated_member_assignments(
    session: AsyncSession, *, programme_id: str = "p-1"
) -> int:
    """Assign every Member-role profile holding a Legacy Summit Django User ID
    mapping into ``programme_id``.

    Migrated profiles carry import-generated ids, so they are found by their
    active legacy mapping, never by fixed id — TheoVH and test1 today, and any
    future dev-machine import on the next seed run. Only the Member role
    qualifies (an imported coach is never programme-assigned here). The upsert
    is keyed on the assignment table's (programme, user, role) unique
    constraint with a deterministic row id, so re-running converges without
    duplicating or altering an existing assignment. Returns how many
    migrated member profiles were found.
    """
    member_role_id = ROLE_IDS["Member"]
    legacy_type_id = EXTERNAL_ID_TYPE_IDS[LEGACY_SUMMIT_DJANGO_USER_ID]
    user_ids = (
        (
            await session.execute(
                select(UserExternalId.user_id)
                .join(UserRole, UserRole.user_id == UserExternalId.user_id)
                .where(
                    UserExternalId.type_id == legacy_type_id,
                    UserExternalId.ended_at.is_(None),
                    UserRole.role_id == member_role_id,
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    for user_id in sorted(user_ids):
        await session.execute(
            pg_insert(ProgrammeAssignment)
            .values(
                id=f"pa-{programme_id}-member-{user_id}",
                programme_id=programme_id,
                user_id=user_id,
                role_id=member_role_id,
            )
            .on_conflict_do_nothing(index_elements=["programme_id", "user_id", "role_id"])
        )
    await session.flush()
    return len(user_ids)
