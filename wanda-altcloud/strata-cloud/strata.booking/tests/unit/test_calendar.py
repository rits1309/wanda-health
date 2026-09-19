"""Calendar terminal-state tests: terminal appointments stay attached.

A no-show or completed appointment leaves its slot ``booked`` forever, so calendar reads
must keep the appointment attached (identity + outcome). Freed lifecycles (``cancelled``,
``rescheduled``) release or re-offer the slot and must never attach — including the
rebook-after-cancel case, where a slot legitimately carries a cancelled record
beside its live successor. States are staged through the real lifecycle writers, never by
poking statuses.
"""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import Appointment, CallRecord, Slot
from strata_core.domains.kernel import Programme

from strata_booking.core import clock as clock_module
from strata_booking.schemas.availability import PatternBody
from strata_booking.schemas.unavailability import CalendarEntry
from strata_booking.services import no_show
from strata_booking.services.authz import ActingContext
from strata_booking.services.availability import create_pattern
from strata_booking.services.booking import book, browse_slots
from strata_booking.services.calendar import admin_calendar, coach_calendar
from strata_booking.services.cancellation import cancel_appointment
from strata_booking.services.rescheduling import reschedule_appointment
from tests.auth import as_principal
from tests.identity_stubs import Admin, Coach, Member, ProgrammeAssignment

NOW = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
WINDOW = {"from_date": date(2026, 10, 1), "to_date": date(2026, 10, 31)}


@pytest.fixture(autouse=True)
def _pin_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock_module.clock.__class__, "now", lambda self: NOW)


async def _world(session: AsyncSession, tag: str) -> tuple[str, str, str]:
    """One programme, one coach with a Monday pattern, two members; ids namespaced by tag."""
    programme_id, coach_id = f"p-cal-{tag}", f"c-cal-{tag}"
    members = [f"m-cal-{tag}-1", f"m-cal-{tag}-2"]
    session.add(Programme(id=programme_id, name=f"Cal {tag}"))
    session.add(Coach(id=coach_id, display_name="Cal Coach", timezone="UTC", languages=["en"]))
    for member_id in members:
        session.add(
            Member(
                id=member_id,
                display_name=f"Cal Member {member_id[-1]}",
                timezone="UTC",
                languages=["en"],
                preferred_language="en",
            )
        )
    assignments = [("coach", coach_id)] + [("member", m) for m in members]
    for kind, uid in assignments:
        session.add(
            ProgrammeAssignment(
                id=f"pa-cal-{tag}-{uid}", programme_id=programme_id, user_kind=kind, user_id=uid
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
    return programme_id, coach_id, members[0]


async def _book_first_slot(session: AsyncSession, member_id: str) -> tuple[str, str, str]:
    """Book the member into their first browsable slot: (appointment_id, slot_id, coach_id)."""
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
    return out.id, out.slot_id, out.coach_id


def _entry(entries: list[CalendarEntry], slot_id: str) -> CalendarEntry:
    """The single calendar entry for the slot — exactly one row must exist per slot."""
    matches = [e for e in entries if e.slot_id == slot_id]
    assert len(matches) == 1, f"expected one entry for {slot_id}, got {len(matches)}"
    return matches[0]


async def _mark_no_show(
    session: AsyncSession, appointment_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive the appointment to ``no_show`` via the real manual-marking service."""
    appointment = await session.get(Appointment, appointment_id)
    assert appointment is not None
    slot = await session.get(Slot, appointment.slot_id)
    assert slot is not None
    after = slot.start_utc + timedelta(minutes=10)
    monkeypatch.setattr(clock_module.clock.__class__, "now", lambda self: after)
    await no_show.mark_no_show(
        session,
        as_principal(appointment.coach_id, "coach"),
        appointment,
        on_behalf_of_coach_id=None,
    )
    assert appointment.status == "no_show"


@pytest.mark.anyio
async def test_no_show_appointment_stays_attached_to_the_calendar(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A no-show appointment keeps identity and outcome on the coach calendar."""
    _, coach_id, member_id = await _world(session, "ns")
    appointment_id, slot_id, _ = await _book_first_slot(session, member_id)
    await _mark_no_show(session, appointment_id, monkeypatch)

    out = await coach_calendar(session, coach_id, **WINDOW)
    entry = _entry(out.entries, slot_id)
    assert entry.status == "booked"
    assert entry.appointment is not None, "terminal appointment vanished from the calendar"
    assert entry.appointment.status == "no_show"
    assert entry.appointment.member_id == member_id
    assert entry.appointment.member_name == "Cal Member 1"


@pytest.mark.anyio
async def test_completed_appointment_stays_attached_to_the_calendar(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completed appointment keeps identity and outcome on the coach calendar."""
    _, coach_id, member_id = await _world(session, "done")
    appointment_id, slot_id, _ = await _book_first_slot(session, member_id)
    appointment = await session.get(Appointment, appointment_id)
    slot = await session.get(Slot, appointment.slot_id if appointment else "")
    assert appointment is not None and slot is not None
    session.add(
        CallRecord(
            coach_id=appointment.coach_id,
            member_id=appointment.member_id,
            initiated_at_utc=slot.start_utc - timedelta(minutes=2),
        )
    )
    await session.commit()
    past_grace = slot.start_utc + timedelta(minutes=30)
    monkeypatch.setattr(clock_module.clock.__class__, "now", lambda self: past_grace)
    await no_show.detect_no_shows(session)
    await session.refresh(appointment)
    assert appointment.status == "completed"

    out = await coach_calendar(session, coach_id, **WINDOW)
    entry = _entry(out.entries, slot_id)
    assert entry.status == "booked"
    assert entry.appointment is not None, "terminal appointment vanished from the calendar"
    assert entry.appointment.status == "completed"
    assert entry.appointment.member_id == member_id
    assert entry.appointment.member_name == "Cal Member 1"


@pytest.mark.anyio
async def test_cancelled_appointment_never_attaches(session: AsyncSession) -> None:
    """Cancelling frees the slot; the freed slot carries no appointment on the calendar."""
    _, coach_id, member_id = await _world(session, "cx")
    appointment_id, slot_id, _ = await _book_first_slot(session, member_id)
    appointment = await session.get(Appointment, appointment_id)
    assert appointment is not None
    await cancel_appointment(
        session,
        as_principal(member_id, "member"),
        appointment,
        reason=None,
        on_behalf_of_coach_id=None,
    )

    out = await coach_calendar(session, coach_id, **WINDOW)
    entry = _entry(out.entries, slot_id)
    assert entry.status == "available"
    assert entry.appointment is None


@pytest.mark.anyio
async def test_rebooked_slot_attaches_only_the_live_appointment(session: AsyncSession) -> None:
    """A cancelled-then-rebooked slot shows only its live appointment, not the ghost."""
    _, coach_id, member_id = await _world(session, "rb")
    other_member = "m-cal-rb-2"
    appointment_id, slot_id, _ = await _book_first_slot(session, member_id)
    appointment = await session.get(Appointment, appointment_id)
    assert appointment is not None
    await cancel_appointment(
        session,
        as_principal(member_id, "member"),
        appointment,
        reason=None,
        on_behalf_of_coach_id=None,
    )
    rebooked, _ = await book(
        session,
        as_principal(other_member, "member"),
        slot_id=slot_id,
        member_id=other_member,
        language_matched=False,
        created_by="member",
        on_behalf_of_coach_id=None,
    )

    out = await coach_calendar(session, coach_id, **WINDOW)
    entry = _entry(out.entries, slot_id)  # one row: the join must not fan out over the ghost
    assert entry.status == "booked"
    assert entry.appointment is not None
    assert entry.appointment.id == rebooked.id
    assert entry.appointment.status == "confirmed"
    assert entry.appointment.member_id == other_member


@pytest.mark.anyio
async def test_rescheduled_appointment_never_attaches(session: AsyncSession) -> None:
    """Rescheduling frees the old slot; only the successor rides the calendar."""
    _, coach_id, member_id = await _world(session, "rs")
    appointment_id, old_slot_id, _ = await _book_first_slot(session, member_id)
    appointment = await session.get(Appointment, appointment_id)
    assert appointment is not None
    new_slots = await browse_slots(session, member_id, language_match=False, **WINDOW)
    successor = await reschedule_appointment(
        session,
        as_principal(member_id, "member"),
        appointment,
        new_slot_id=new_slots[0].slot_id,
    )

    out = await coach_calendar(session, coach_id, **WINDOW)
    old_entry = _entry(out.entries, old_slot_id)
    assert old_entry.status == "available"
    assert old_entry.appointment is None
    new_entry = _entry(out.entries, successor.slot_id)
    assert new_entry.appointment is not None
    assert new_entry.appointment.status == "confirmed"


@pytest.mark.anyio
async def test_slot_rebooked_after_reschedule_attaches_only_the_live_appointment(
    session: AsyncSession,
) -> None:
    """A slot freed by a reschedule and rebooked shows only its live appointment."""
    _, coach_id, member_id = await _world(session, "rr")
    other_member = "m-cal-rr-2"
    appointment_id, old_slot_id, _ = await _book_first_slot(session, member_id)
    appointment = await session.get(Appointment, appointment_id)
    assert appointment is not None
    new_slots = await browse_slots(session, member_id, language_match=False, **WINDOW)
    await reschedule_appointment(
        session,
        as_principal(member_id, "member"),
        appointment,
        new_slot_id=new_slots[0].slot_id,
    )
    rebooked, _ = await book(
        session,
        as_principal(other_member, "member"),
        slot_id=old_slot_id,
        member_id=other_member,
        language_matched=False,
        created_by="member",
        on_behalf_of_coach_id=None,
    )

    out = await coach_calendar(session, coach_id, **WINDOW)
    # One row: the join must not fan out over the frozen rescheduled record.
    entry = _entry(out.entries, old_slot_id)
    assert entry.status == "booked"
    assert entry.appointment is not None
    assert entry.appointment.id == rebooked.id
    assert entry.appointment.status == "confirmed"
    assert entry.appointment.member_id == other_member


@pytest.mark.anyio
async def test_admin_calendar_carries_terminal_appointments(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The admin combined calendar carries terminal appointments (it reads via coach_calendar)."""
    programme_id, coach_id, member_id = await _world(session, "adm")
    admin_id = "a-cal-adm"
    session.add(Admin(id=admin_id))
    session.add(
        ProgrammeAssignment(
            id=f"pa-cal-adm-{admin_id}",
            programme_id=programme_id,
            user_kind="admin",
            user_id=admin_id,
        )
    )
    await session.flush()
    appointment_id, slot_id, _ = await _book_first_slot(session, member_id)
    await _mark_no_show(session, appointment_id, monkeypatch)

    out = await admin_calendar(
        session, as_principal(admin_id, "admin"), coach_ids=[coach_id], layers=None, **WINDOW
    )
    (coach_view,) = [c for c in out.coaches if c.coach_id == coach_id]
    entry = _entry(coach_view.entries, slot_id)
    assert entry.appointment is not None, "terminal appointment vanished from the admin calendar"
    assert entry.appointment.status == "no_show"
