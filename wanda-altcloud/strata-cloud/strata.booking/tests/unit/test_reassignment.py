"""Reassignment tests: same-time options, language criteria, the handover."""

from datetime import UTC, date, datetime, time

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import (
    Appointment,
    Notification,
    OutboxEvent,
    ReassignmentEvent,
    Reminder,
    Slot,
)
from strata_core.domains.kernel import Programme

from strata_booking.core import clock as clock_module
from strata_booking.core.security import Principal
from strata_booking.schemas.availability import PatternBody
from strata_booking.services import reassignment
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


async def _fixture(session: AsyncSession, tag: str, *, notify: bool = True) -> tuple[str, str]:
    """Programme (configurable notification) + 3 coaches on the same pattern (3rd es-only)."""
    programme_id, member_id = f"p-ra-{tag}", f"m-ra-{tag}"
    session.add(
        Programme(id=programme_id, name=f"Ra {tag}", reassignment_notification_enabled=notify)
    )
    session.add(
        Member(
            id=member_id,
            display_name="M",
            timezone="UTC",
            languages=["en"],
            preferred_language="en",
        )
    )
    session.add(
        ProgrammeAssignment(
            id=f"pa-ra-{tag}-m", programme_id=programme_id, user_kind="member", user_id=member_id
        )
    )
    for i, languages in ((1, ["en"]), (2, ["en"]), (3, ["es"])):
        coach_id = f"c-ra-{tag}-{i}"
        session.add(
            Coach(id=coach_id, display_name=f"Coach {i}", timezone="UTC", languages=languages)
        )
        session.add(
            ProgrammeAssignment(
                id=f"pa-ra-{tag}-{i}",
                programme_id=programme_id,
                user_kind="coach",
                user_id=coach_id,
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
    return programme_id, member_id


async def _booked(
    session: AsyncSession, tag: str, *, language_matched: bool = False, notify: bool = True
) -> Appointment:
    _, member_id = await _fixture(session, tag, notify=notify)
    slots = await browse_slots(
        session, member_id, language_match=False, coach_id=f"c-ra-{tag}-1", **WINDOW
    )
    out, _ = await book(
        session,
        as_principal(member_id, "member"),
        slot_id=slots[0].slot_id,
        member_id=member_id,
        language_matched=language_matched,
        created_by="member",
        on_behalf_of_coach_id=None,
    )
    appointment = await session.get(Appointment, out.id)
    assert appointment is not None
    return appointment


def _coach(appointment: Appointment) -> Principal:
    return as_principal(appointment.coach_id, "coach")


@pytest.mark.anyio
async def test_options_are_exact_time_and_language_filtered(session: AsyncSession) -> None:
    """Reassignment options are exact-time, language-filtered and exclude the current coach."""
    appointment = await _booked(session, "o1", language_matched=True)
    options = await reassignment.reassignment_options(session, appointment)
    # Coach 2 (en) qualifies; coach 3 (es-only) is excluded by the original criteria; the
    # current coach never appears.
    assert [o.coach_id for o in options] == ["c-ra-o1-2"]

    slot = await session.get(Slot, appointment.slot_id)
    assert slot is not None
    for option in options:
        candidate = await session.get(Slot, option.slot_id)
        assert candidate is not None
        assert candidate.start_utc == slot.start_utc
        assert candidate.duration_minutes == slot.duration_minutes


@pytest.mark.anyio
async def test_reassign_moves_the_same_record_and_audits(session: AsyncSession) -> None:
    """Reassign moves the same appointment record to the new coach and audits the change."""
    appointment = await _booked(session, "r1")
    old_slot_id, old_coach_id = appointment.slot_id, appointment.coach_id
    options = await reassignment.reassignment_options(session, appointment)
    target = options[0]

    await reassignment.reassign(
        session,
        _coach(appointment),
        appointment,
        new_slot_id=target.slot_id,
        on_behalf_of_coach_id=None,
    )
    assert appointment.status == "confirmed"  # status and member unchanged
    assert appointment.coach_id == target.coach_id
    assert appointment.slot_id == target.slot_id
    old_slot = await session.get(Slot, old_slot_id)
    new_slot = await session.get(Slot, target.slot_id)
    assert old_slot is not None and old_slot.status == "available"
    assert new_slot is not None and new_slot.status == "booked"

    audit = (
        await session.execute(
            select(ReassignmentEvent).where(ReassignmentEvent.appointment_id == appointment.id)
        )
    ).scalar_one()
    assert (audit.from_coach_id, audit.to_coach_id) == (old_coach_id, target.coach_id)

    # The pending coach reminder follows the new coach; member notified (default config on).
    coach_reminder = (
        await session.execute(
            select(Reminder).where(
                Reminder.appointment_id == appointment.id,
                Reminder.recipient_kind == "coach",
                Reminder.status == "pending",
            )
        )
    ).scalar_one()
    assert coach_reminder.recipient_id == target.coach_id

    note = (
        await session.execute(
            select(Notification).where(
                Notification.recipient_id == appointment.member_id,
                Notification.notification_type == "appointment_reassigned",
            )
        )
    ).scalar_one()
    assert note.payload["to_coach_id"] == target.coach_id

    events = (
        (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == "AppointmentReassigned")
            )
        )
        .scalars()
        .all()
    )
    assert any(e.payload.get("appointment_id") == appointment.id for e in events)


@pytest.mark.anyio
async def test_member_notification_respects_programme_config(session: AsyncSession) -> None:
    """The member reassignment notification respects the programme's notify config."""
    appointment = await _booked(session, "n1", notify=False)
    options = await reassignment.reassignment_options(session, appointment)
    await reassignment.reassign(
        session,
        _coach(appointment),
        appointment,
        new_slot_id=options[0].slot_id,
        on_behalf_of_coach_id=None,
    )
    notes = (
        (
            await session.execute(
                select(Notification).where(
                    Notification.recipient_id == appointment.member_id,
                    Notification.notification_type == "appointment_reassigned",
                )
            )
        )
        .scalars()
        .all()
    )
    assert notes == []
    events = (
        (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == "AppointmentReassigned")
            )
        )
        .scalars()
        .all()
    )
    mine = [e for e in events if e.payload.get("appointment_id") == appointment.id]
    assert mine and mine[0].payload.get("member_notified") is False


@pytest.mark.anyio
async def test_reassign_rejects_a_different_time_slot(session: AsyncSession) -> None:
    """Reassign refuses a slot at a different time, even for an eligible coach."""
    appointment = await _booked(session, "t1")
    # A later slot of another eligible coach — right coach, wrong time.
    later = (
        (
            await session.execute(
                select(Slot)
                .where(Slot.coach_id == "c-ra-t1-2", Slot.status == "available")
                .order_by(Slot.start_utc.desc())
            )
        )
        .scalars()
        .first()
    )
    assert later is not None
    with pytest.raises(HTTPException) as exc:
        await reassignment.reassign(
            session,
            _coach(appointment),
            appointment,
            new_slot_id=later.id,
            on_behalf_of_coach_id=None,
        )
    assert exc.value.status_code == 422
    assert "same" in exc.value.detail


@pytest.mark.anyio
async def test_no_candidates_means_empty_options(session: AsyncSession) -> None:
    # es-preferring criteria with only coach 3 speaking es — but coach 3's same-time slot is
    # what qualifies; exclude it by booking language-matched es→ only c3 qualifies... so book
    # with criteria no other coach can meet: language-matched where member prefers a language
    # only the current coach speaks.
    """When no other coach can meet the original criteria, options are empty."""
    programme_id, member_id = "p-ra-e1", "m-ra-e1"
    session.add(Programme(id=programme_id, name="Ra e1"))
    session.add(
        Member(
            id=member_id,
            display_name="M",
            timezone="UTC",
            languages=["es"],
            preferred_language="es",
        )
    )
    session.add(Coach(id="c-ra-e1-1", display_name="C", timezone="UTC", languages=["es", "en"]))
    for kind, uid in (("member", member_id), ("coach", "c-ra-e1-1")):
        session.add(
            ProgrammeAssignment(
                id=f"pa-ra-e1-{kind}", programme_id=programme_id, user_kind=kind, user_id=uid
            )
        )
    await session.flush()
    await create_pattern(
        session,
        ActingContext(coach_id="c-ra-e1-1", acting_user_id="c-ra-e1-1", on_behalf_of_coach_id=None),
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
        language_matched=True,
        created_by="member",
        on_behalf_of_coach_id=None,
    )
    appointment = await session.get(Appointment, out.id)
    assert appointment is not None
    assert await reassignment.reassignment_options(session, appointment) == []
