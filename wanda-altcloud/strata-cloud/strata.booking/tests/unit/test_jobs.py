"""Jobs module unit tests: reminder sending idempotency + slot top-up."""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import (
    Appointment,
    AvailabilityPattern,
    Notification,
    OutboxEvent,
    Reminder,
    Slot,
)
from strata_core.domains.kernel import Programme

from strata_booking.core import clock as clock_module
from strata_booking.schemas.availability import PatternBody
from strata_booking.services import jobs
from strata_booking.services.authz import ActingContext
from strata_booking.services.availability import create_pattern
from strata_booking.services.booking import book, browse_slots
from strata_booking.services.cancellation import cancel_appointment
from tests.auth import as_principal
from tests.identity_stubs import Coach, Member, ProgrammeAssignment

NOW = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
WINDOW = {"from_date": date(2026, 10, 1), "to_date": date(2026, 10, 31)}


@pytest.fixture(autouse=True)
def _pin_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock_module.clock.__class__, "now", lambda self: NOW)


async def _fixture(session: AsyncSession, tag: str) -> str:
    programme_id, member_id, coach_id = f"p-job-{tag}", f"m-job-{tag}", f"c-job-{tag}"
    if await session.get(Programme, programme_id) is None:
        session.add(Programme(id=programme_id, name=f"Job {tag}"))
        session.add(
            Member(
                id=member_id,
                display_name="M",
                timezone="UTC",
                languages=["en"],
                preferred_language="en",
            )
        )
        session.add(Coach(id=coach_id, display_name="C", timezone="UTC", languages=["en"]))
        for kind, uid in (("member", member_id), ("coach", coach_id)):
            session.add(
                ProgrammeAssignment(
                    id=f"pa-job-{tag}-{kind}",
                    programme_id=programme_id,
                    user_kind=kind,
                    user_id=uid,
                )
            )
        await session.flush()
        await create_pattern(
            session,
            ActingContext(coach_id=coach_id, acting_user_id=coach_id, on_behalf_of_coach_id=None),
            PatternBody(
                days_of_week=[0],
                start_time_local=time(9, 0),
                end_time_local=time(10, 0),
                slot_duration_minutes=20,
                timezone="UTC",
                active_from=date(2026, 10, 1),
                active_to=date(2026, 10, 31),
            ),
        )
    return member_id


async def _book(session: AsyncSession, tag: str) -> Appointment:
    member_id = await _fixture(session, tag)
    slots = await browse_slots(session, member_id, language_match=False, **WINDOW)
    out, _ = await book(
        session,
        as_principal(member_id, "member"),
        slot_id=slots[0].slot_id,
        member_id=member_id,
        language_matched=False,
        created_by="member",
        on_behalf_of_coach_id=None,
    )
    appointment = await session.get(Appointment, out.id)
    assert appointment is not None
    return appointment


async def _reminders(session: AsyncSession, appointment_id: str) -> list[Reminder]:
    return list(
        (await session.execute(select(Reminder).where(Reminder.appointment_id == appointment_id)))
        .scalars()
        .all()
    )


@pytest.mark.anyio
async def test_due_reminders_send_once_and_rerun_is_a_noop(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Due reminders send exactly once: the job rerun is a no-op."""
    appointment = await _book(session, "r1")
    slot = await session.get(Slot, appointment.slot_id)
    assert slot is not None
    # Reminders are due lead-time (24h default) before start; move "now" past that point.
    due_time = slot.start_utc - timedelta(hours=2)
    monkeypatch.setattr(clock_module.clock.__class__, "now", lambda self: due_time)

    first = await jobs.send_due_reminders(session)
    assert first["reminders_sent"] >= 2  # this appointment's member + coach (+ other suites')

    for reminder in await _reminders(session, appointment.id):
        assert reminder.status == "sent" and reminder.sent_at == due_time

    notes = (
        (
            await session.execute(
                select(Notification).where(
                    Notification.notification_type == "appointment_reminder",
                    Notification.recipient_id.in_([appointment.member_id, appointment.coach_id]),
                )
            )
        )
        .scalars()
        .all()
    )
    assert {(n.recipient_kind, n.recipient_id) for n in notes} == {
        ("member", appointment.member_id),
        ("coach", appointment.coach_id),
    }
    events = (
        (await session.execute(select(OutboxEvent).where(OutboxEvent.event_type == "ReminderSent")))
        .scalars()
        .all()
    )
    assert sum(1 for e in events if e.payload.get("appointment_id") == appointment.id) == 2

    # Retry safety: a second run sends nothing for this appointment.
    second = await jobs.send_due_reminders(session)
    assert second["reminders_sent"] == 0


@pytest.mark.anyio
async def test_reminders_for_non_confirmed_appointments_are_cancelled_not_sent(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reminders for no-longer-confirmed appointments are cancelled, never sent."""
    appointment = await _book(session, "c1")
    slot = await session.get(Slot, appointment.slot_id)
    assert slot is not None
    # Cancel outside the window (allowed), then force the reminder rows back to pending to
    # simulate a flow that forgot to cancel them — the job must not send for a dead appointment.
    await cancel_appointment(
        session,
        as_principal(appointment.member_id, "member"),
        appointment,
        reason=None,
        on_behalf_of_coach_id=None,
    )
    for reminder in await _reminders(session, appointment.id):
        reminder.status = "pending"
    await session.commit()

    due_time = slot.start_utc - timedelta(hours=2)
    monkeypatch.setattr(clock_module.clock.__class__, "now", lambda self: due_time)
    result = await jobs.send_due_reminders(session)
    assert result["reminders_cancelled"] >= 2
    for reminder in await _reminders(session, appointment.id):
        assert reminder.status == "cancelled"


@pytest.mark.anyio
async def test_generate_slots_tops_up_to_the_rolling_horizon(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A long-running pattern: creation materialises to the 4-week horizon (until 2026-10-29).
    """The slot-generation job tops patterns up to the rolling horizon."""
    coach_id = "c-job-g1"
    session.add(Programme(id="p-job-g1", name="Job g1"))
    session.add(Coach(id=coach_id, display_name="C", timezone="UTC", languages=["en"]))
    session.add(
        ProgrammeAssignment(
            id="pa-job-g1-c", programme_id="p-job-g1", user_kind="coach", user_id=coach_id
        )
    )
    await session.flush()
    await create_pattern(
        session,
        ActingContext(coach_id=coach_id, acting_user_id=coach_id, on_behalf_of_coach_id=None),
        PatternBody(
            days_of_week=[0],
            start_time_local=time(9, 0),
            end_time_local=time(10, 0),
            slot_duration_minutes=20,
            timezone="UTC",
            active_from=date(2026, 10, 1),
            active_to=date(2026, 12, 31),
        ),
    )
    pattern = (
        await session.execute(
            select(AvailabilityPattern).where(AvailabilityPattern.coach_id == coach_id)
        )
    ).scalar_one()
    watermark_at_creation = pattern.generation_watermark
    assert watermark_at_creation == date(2026, 10, 29)

    async def coach_slots() -> int:
        rows = await session.execute(select(Slot.id).where(Slot.coach_id == coach_id))
        return len(rows.scalars().all())

    # Two weeks later the horizon has rolled forward — the job must fill the gap. (The job
    # processes every active pattern in the shared test DB, so assert on this coach's slots.)
    before = await coach_slots()
    later = NOW + timedelta(weeks=2)
    monkeypatch.setattr(clock_module.clock.__class__, "now", lambda self: later)
    result = await jobs.generate_slots(session)
    assert result["slots_created"] >= 6
    assert await coach_slots() - before == 6  # Mondays 2 Nov + 9 Nov, 3 twenty-minute slots each
    await session.refresh(pattern)
    assert pattern.generation_watermark == date(2026, 11, 12)

    # Idempotent: an immediate rerun creates nothing new for this coach.
    again = await jobs.generate_slots(session)
    assert again["slots_created"] == 0
    assert await coach_slots() - before == 6
