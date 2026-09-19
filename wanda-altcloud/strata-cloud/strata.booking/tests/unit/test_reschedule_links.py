"""Tokened reschedule-link resolver tests."""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import Appointment, Notification
from strata_core.domains.kernel import Programme

from strata_booking.core import clock as clock_module
from strata_booking.schemas.availability import PatternBody
from strata_booking.services import rescheduling
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


async def _booked_appointment(session: AsyncSession, tag: str) -> Appointment:
    programme_id, member_id, coach_id = f"p-rl-{tag}", f"m-rl-{tag}", f"c-rl-{tag}"
    session.add(Programme(id=programme_id, name=f"Rl {tag}"))
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
                id=f"pa-rl-{tag}-{kind}", programme_id=programme_id, user_kind=kind, user_id=uid
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
    assert appointment is not None
    return appointment


async def _request_link_token(session: AsyncSession, appointment: Appointment) -> str:
    coach = as_principal(appointment.coach_id, "coach")
    await rescheduling.request_reschedule(session, coach, appointment, on_behalf_of_coach_id=None)
    note = (
        await session.execute(
            select(Notification).where(
                Notification.recipient_id == appointment.member_id,
                Notification.notification_type == "reschedule_requested",
            )
        )
    ).scalar_one()
    return str(note.payload["link"]).split("token=")[1]


@pytest.mark.anyio
async def test_resolve_returns_appointment_and_same_coach_slots(session: AsyncSession) -> None:
    """Resolving a reschedule link returns the appointment and same-coach bookable slots."""
    appointment = await _booked_appointment(session, "ok")
    token = await _request_link_token(session, appointment)

    out = await rescheduling.resolve_link(session, token)
    assert out.appointment.id == appointment.id
    assert out.appointment.member_name == "M"  # kernel name on the tokened payload
    assert out.slots, "expected bookable slots for the same coach"
    assert all(s.coach_id == appointment.coach_id for s in out.slots)
    assert all(s.slot_id != appointment.slot_id for s in out.slots)


@pytest.mark.anyio
async def test_resolved_link_dies_once_fulfilled(session: AsyncSession) -> None:
    """A reschedule link is spent once fulfilled and cannot be resolved again."""
    appointment = await _booked_appointment(session, "used")
    token = await _request_link_token(session, appointment)
    target = (await rescheduling.resolve_link(session, token)).slots[0]

    member = as_principal(appointment.member_id, "member")
    await rescheduling.reschedule_appointment(
        session, member, appointment, new_slot_id=target.slot_id
    )
    with pytest.raises(HTTPException) as exc:
        await rescheduling.resolve_link(session, token)
    assert exc.value.status_code == 410


@pytest.mark.anyio
async def test_expired_link_is_410_and_garbage_is_404(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An expired reschedule link is 410; a garbage token is 404."""
    appointment = await _booked_appointment(session, "exp")
    token = await _request_link_token(session, appointment)

    monkeypatch.setattr(clock_module.clock.__class__, "now", lambda self: NOW + timedelta(days=8))
    with pytest.raises(HTTPException) as expired:
        await rescheduling.resolve_link(session, token)
    assert expired.value.status_code == 410

    monkeypatch.setattr(clock_module.clock.__class__, "now", lambda self: NOW)
    with pytest.raises(HTTPException) as garbage:
        await rescheduling.resolve_link(session, "garbage")
    assert garbage.value.status_code == 404
