"""Suggested-alternatives engine unit tests: ordering, language, accept/reject."""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import Appointment, Notification, OutboxEvent, Slot
from strata_core.domains.kernel import Programme

from strata_booking.core import clock as clock_module
from strata_booking.schemas.availability import PatternBody
from strata_booking.services import alternatives
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


async def _fixture(session: AsyncSession, tag: str, *, coaches: int = 3) -> tuple[str, str]:
    """Programme with N coaches (last one es-only) sharing a Monday 9–10 pattern + en member."""
    programme_id, member_id = f"p-alt-{tag}", f"m-alt-{tag}"
    if await session.get(Programme, programme_id) is None:
        session.add(Programme(id=programme_id, name=f"Alt {tag}"))
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
                id=f"pa-alt-{tag}-m",
                programme_id=programme_id,
                user_kind="member",
                user_id=member_id,
            )
        )
        for i in range(1, coaches + 1):
            coach_id = f"c-alt-{tag}-{i}"
            languages = ["es"] if i == coaches and coaches > 2 else ["en"]
            session.add(
                Coach(id=coach_id, display_name=f"Coach {i}", timezone="UTC", languages=languages)
            )
            session.add(
                ProgrammeAssignment(
                    id=f"pa-alt-{tag}-{i}",
                    programme_id=programme_id,
                    user_kind="coach",
                    user_id=coach_id,
                )
            )
            await session.flush()
            await create_pattern(
                session,
                ActingContext(
                    coach_id=coach_id, acting_user_id=coach_id, on_behalf_of_coach_id=None
                ),
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


async def _book_first_slot(
    session: AsyncSession, tag: str, *, language_matched: bool
) -> Appointment:
    _, member_id = await _fixture(session, tag)
    coach_id = f"c-alt-{tag}-1"
    slots = await browse_slots(
        session, member_id, language_match=False, coach_id=coach_id, **WINDOW
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


async def _suggestion_email(session: AsyncSession, member_id: str) -> Notification:
    notes = (
        (
            await session.execute(
                select(Notification).where(
                    Notification.recipient_id == member_id,
                    Notification.notification_type == "appointment_alternatives_suggested",
                )
            )
        )
        .scalars()
        .all()
    )
    assert notes, "expected a suggestion email"
    return notes[-1]


@pytest.mark.anyio
async def test_ordering_and_language_condition(session: AsyncSession) -> None:
    """Suggested alternatives are ordered and respect the original language condition."""
    appointment = await _book_first_slot(session, "o1", language_matched=True)
    coach = as_principal(appointment.coach_id, "coach")
    await cancel_appointment(
        session, coach, appointment, reason="emergency", on_behalf_of_coach_id=None
    )

    email = await _suggestion_email(session, appointment.member_id)
    alts = email.payload["alternatives"]
    assert alts, "expected alternatives"
    # First preference: a different coach at the exact original time.
    assert alts[0]["kind"] == "same_time_other_coach"
    assert alts[0]["coach_id"] == "c-alt-o1-2"
    # The es-only coach never appears (original booking was language-matched, member prefers en).
    assert all(a["coach_id"] != "c-alt-o1-3" for a in alts)
    # Same-day suggestions follow, ordered by proximity to the original time.
    same_day = [a for a in alts if a["kind"] == "same_day_window"]
    assert same_day
    assert "booking_page_link" in email.payload

    events = (
        (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "AppointmentAlternativesSuggested"
                )
            )
        )
        .scalars()
        .all()
    )
    assert any(e.payload.get("appointment_id") == appointment.id for e in events)


@pytest.mark.anyio
async def test_accept_books_the_alternative(session: AsyncSession) -> None:
    """Accepting an alternatives link books the suggested slot for the member."""
    appointment = await _book_first_slot(session, "a1", language_matched=False)
    coach = as_principal(appointment.coach_id, "coach")
    await cancel_appointment(session, coach, appointment, reason=None, on_behalf_of_coach_id=None)

    email = await _suggestion_email(session, appointment.member_id)
    token = str(email.payload["alternatives"][0]["link"]).split("token=")[1]

    details = await alternatives.describe(session, token)
    assert details["slot_available"] is True

    new_appointment_id = await alternatives.accept(session, token)
    new_appointment = await session.get(Appointment, new_appointment_id)
    assert new_appointment is not None and new_appointment.status == "confirmed"
    assert new_appointment.member_id == appointment.member_id
    slot = await session.get(Slot, new_appointment.slot_id)
    assert slot is not None and slot.status == "booked"

    with pytest.raises(HTTPException) as exc:  # accept is one-shot
        await alternatives.accept(session, token)
    assert exc.value.status_code == 409


@pytest.mark.anyio
async def test_accept_fails_gracefully_when_slot_taken(session: AsyncSession) -> None:
    """Accepting an alternative whose slot was taken meanwhile fails gracefully."""
    appointment = await _book_first_slot(session, "t1", language_matched=False)
    coach = as_principal(appointment.coach_id, "coach")
    await cancel_appointment(session, coach, appointment, reason=None, on_behalf_of_coach_id=None)
    email = await _suggestion_email(session, appointment.member_id)
    first = email.payload["alternatives"][0]
    token = str(first["link"]).split("token=")[1]

    # Another member takes the suggested slot first.
    session.add(
        Member(
            id="m-alt-t1-2",
            display_name="Rival",
            timezone="UTC",
            languages=["en"],
            preferred_language="en",
        )
    )
    session.add(
        ProgrammeAssignment(
            id="pa-alt-t1-m2", programme_id="p-alt-t1", user_kind="member", user_id="m-alt-t1-2"
        )
    )
    await session.commit()
    await book(
        session,
        as_principal("m-alt-t1-2", "member"),
        slot_id=str(first["slot_id"]),
        member_id="m-alt-t1-2",
        language_matched=False,
        created_by="member",
        on_behalf_of_coach_id=None,
    )

    with pytest.raises(HTTPException) as exc:
        await alternatives.accept(session, token)
    assert exc.value.status_code == 409
    assert "booking page" in exc.value.detail


@pytest.mark.anyio
async def test_accept_desynced_slot_degrades_to_409(session: AsyncSession) -> None:
    """Accepting an alternative whose desynced slot hides a confirmed booking is 409."""
    appointment = await _book_first_slot(session, "d1", language_matched=False)
    coach = as_principal(appointment.coach_id, "coach")
    await cancel_appointment(session, coach, appointment, reason=None, on_behalf_of_coach_id=None)
    email = await _suggestion_email(session, appointment.member_id)
    first = email.payload["alternatives"][0]
    token = str(first["link"]).split("token=")[1]

    # A rival member takes the suggested slot; then the denormalized status lies available,
    # so accept's own status guard passes and only the unique index can refuse the booking.
    session.add(
        Member(
            id="m-alt-d1-2",
            display_name="Rival",
            timezone="UTC",
            languages=["en"],
            preferred_language="en",
        )
    )
    session.add(
        ProgrammeAssignment(
            id="pa-alt-d1-m2", programme_id="p-alt-d1", user_kind="member", user_id="m-alt-d1-2"
        )
    )
    await session.commit()
    await book(
        session,
        as_principal("m-alt-d1-2", "member"),
        slot_id=str(first["slot_id"]),
        member_id="m-alt-d1-2",
        language_matched=False,
        created_by="member",
        on_behalf_of_coach_id=None,
    )
    desynced = await session.get(Slot, str(first["slot_id"]))
    assert desynced is not None
    desynced.status = "available"
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await alternatives.accept(session, token)
    assert exc.value.status_code == 409
    assert exc.value.detail == "Slot is no longer available"


@pytest.mark.anyio
async def test_reject_books_nothing(session: AsyncSession) -> None:
    """Rejecting an alternatives link books nothing and the link records the outcome."""
    appointment = await _book_first_slot(session, "r1", language_matched=False)
    coach = as_principal(appointment.coach_id, "coach")
    await cancel_appointment(session, coach, appointment, reason=None, on_behalf_of_coach_id=None)
    email = await _suggestion_email(session, appointment.member_id)
    token = str(email.payload["alternatives"][0]["link"]).split("token=")[1]

    await alternatives.reject(session, token)
    details = await alternatives.describe(session, token)
    assert details["status"] == "rejected"
    with pytest.raises(HTTPException) as exc:
        await alternatives.accept(session, token)
    assert exc.value.status_code == 410


@pytest.mark.anyio
async def test_no_alternatives_still_sends_booking_link(session: AsyncSession) -> None:
    # A single coach with a single 20-minute window: no other slot can be suggested.
    """With no slots to suggest, the member still receives a booking link."""
    programme_id, member_id = "p-alt-n1", "m-alt-n1"
    session.add(Programme(id=programme_id, name="Alt n1"))
    session.add(
        Member(
            id=member_id,
            display_name="M",
            timezone="UTC",
            languages=["en"],
            preferred_language="en",
        )
    )
    session.add(Coach(id="c-alt-n1", display_name="Solo", timezone="UTC", languages=["en"]))
    for kind, uid in (("member", member_id), ("coach", "c-alt-n1")):
        session.add(
            ProgrammeAssignment(
                id=f"pa-alt-n1-{kind}", programme_id=programme_id, user_kind=kind, user_id=uid
            )
        )
    await session.flush()
    await create_pattern(
        session,
        ActingContext(coach_id="c-alt-n1", acting_user_id="c-alt-n1", on_behalf_of_coach_id=None),
        PatternBody(
            days_of_week=[0],
            start_time_local=time(9, 0),
            end_time_local=time(9, 20),
            slot_duration_minutes=20,
            timezone="UTC",
            active_from=date(2026, 10, 5),
            active_to=date(2026, 10, 5),
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
    await cancel_appointment(
        session,
        as_principal("c-alt-n1", "coach"),
        appointment,
        reason=None,
        on_behalf_of_coach_id=None,
    )
    email = await _suggestion_email(session, member_id)
    # The freed original slot is never suggested back; with nothing else, alternatives are empty
    # but the booking-page link is always present.
    assert email.payload["alternatives"] == []
    assert email.payload["booking_page_link"]


@pytest.mark.anyio
async def test_expired_link_is_410(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """An expired alternatives link is 410 Gone."""
    appointment = await _book_first_slot(session, "x1", language_matched=False)
    coach = as_principal(appointment.coach_id, "coach")
    await cancel_appointment(session, coach, appointment, reason=None, on_behalf_of_coach_id=None)
    email = await _suggestion_email(session, appointment.member_id)
    token = str(email.payload["alternatives"][0]["link"]).split("token=")[1]

    monkeypatch.setattr(clock_module.clock.__class__, "now", lambda self: NOW + timedelta(days=8))
    with pytest.raises(HTTPException) as exc:
        await alternatives.describe(session, token)
    assert exc.value.status_code == 410
