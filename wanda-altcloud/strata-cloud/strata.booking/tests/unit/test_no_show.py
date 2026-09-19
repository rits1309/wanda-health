"""No-show tests: manual marking + system detection via the call-record seam."""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import Appointment, CallRecord, Notification, OutboxEvent, Slot
from strata_core.domains.kernel import Programme

from strata_booking.core import clock as clock_module
from strata_booking.core.security import Principal
from strata_booking.schemas.availability import PatternBody
from strata_booking.services import no_show
from strata_booking.services.authz import ActingContext
from strata_booking.services.availability import create_pattern
from strata_booking.services.booking import book, browse_slots
from tests.auth import as_principal
from tests.identity_stubs import Coach, Member, ProgrammeAssignment

NOW = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
WINDOW = {"from_date": date(2026, 10, 1), "to_date": date(2026, 10, 31)}


@pytest.fixture(autouse=True)
def _pin_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock_module.clock.__class__, "now", lambda self: NOW)


async def _booked(session: AsyncSession, tag: str) -> tuple[Appointment, Slot]:
    programme_id, member_id, coach_id = f"p-ns-{tag}", f"m-ns-{tag}", f"c-ns-{tag}"
    session.add(Programme(id=programme_id, name=f"Ns {tag}"))
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
                id=f"pa-ns-{tag}-{kind}", programme_id=programme_id, user_kind=kind, user_id=uid
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
    slot = await session.get(Slot, out.slot_id)
    assert appointment is not None and slot is not None
    return appointment, slot


def _coach(appointment: Appointment) -> Principal:
    return as_principal(appointment.coach_id, "coach")


@pytest.mark.anyio
async def test_manual_marking_after_start(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A coach can mark a no-show once the appointment has started."""
    appointment, slot = await _booked(session, "m1")
    after = slot.start_utc + timedelta(minutes=10)
    monkeypatch.setattr(clock_module.clock.__class__, "now", lambda self: after)

    await no_show.mark_no_show(
        session, _coach(appointment), appointment, on_behalf_of_coach_id=None
    )
    assert appointment.status == "no_show"
    assert appointment.no_show_detection == "coach"
    assert appointment.no_show_detected_at == after

    events = (
        (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == "AppointmentNoShowMarked")
            )
        )
        .scalars()
        .all()
    )
    assert any(e.payload.get("appointment_id") == appointment.id for e in events)

    # Terminal: cannot mark twice.
    with pytest.raises(HTTPException) as exc:
        await no_show.mark_no_show(
            session, _coach(appointment), appointment, on_behalf_of_coach_id=None
        )
    assert exc.value.status_code == 409


@pytest.mark.anyio
async def test_manual_marking_before_start_is_409(session: AsyncSession) -> None:
    """Marking a no-show before the start is refused with 409."""
    appointment, _ = await _booked(session, "m2")
    with pytest.raises(HTTPException) as exc:  # pinned NOW is before the slot start
        await no_show.mark_no_show(
            session, _coach(appointment), appointment, on_behalf_of_coach_id=None
        )
    assert exc.value.status_code == 409
    assert "not started" in exc.value.detail


@pytest.mark.anyio
async def test_detection_marks_no_show_and_notifies_both(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Detection marks a stale appointment no-show and notifies coach and member."""
    appointment, slot = await _booked(session, "d1")
    past_grace = slot.start_utc + timedelta(minutes=30)  # default grace is 15
    monkeypatch.setattr(clock_module.clock.__class__, "now", lambda self: past_grace)

    result = await no_show.detect_no_shows(session)
    assert result["no_shows_detected"] >= 1

    await session.refresh(appointment)
    assert appointment.status == "no_show"
    assert appointment.no_show_detection == "system"

    notes = (
        (
            await session.execute(
                select(Notification).where(
                    Notification.notification_type == "appointment_no_show",
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
        (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == "AppointmentNoShowDetected")
            )
        )
        .scalars()
        .all()
    )
    assert any(e.payload.get("appointment_id") == appointment.id for e in events)


@pytest.mark.anyio
async def test_detection_completes_when_a_call_happened(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Detection completes (not no-shows) an appointment whose call happened."""
    appointment, slot = await _booked(session, "d2")
    session.add(
        CallRecord(
            coach_id=appointment.coach_id,
            member_id=appointment.member_id,
            initiated_at_utc=slot.start_utc - timedelta(minutes=2),  # inside the −5 min lead
        )
    )
    await session.commit()
    past_grace = slot.start_utc + timedelta(minutes=30)
    monkeypatch.setattr(clock_module.clock.__class__, "now", lambda self: past_grace)

    result = await no_show.detect_no_shows(session)
    assert result["appointments_completed"] >= 1
    await session.refresh(appointment)
    assert appointment.status == "completed"


@pytest.mark.anyio
async def test_detection_waits_for_the_grace_period(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Detection leaves appointments alone until the grace period has elapsed."""
    appointment, slot = await _booked(session, "d3")
    inside_grace = slot.start_utc + timedelta(minutes=10)  # started, but grace (15) not elapsed
    monkeypatch.setattr(clock_module.clock.__class__, "now", lambda self: inside_grace)

    await no_show.detect_no_shows(session)
    await session.refresh(appointment)
    assert appointment.status == "confirmed"
