"""Booking service unit tests: browse filtering, idempotency, contention, reminders, events."""

from datetime import UTC, date, datetime, time

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import Appointment, Notification, OutboxEvent, Reminder, Slot

from strata_booking.core import clock as clock_module
from strata_booking.schemas.availability import PatternBody
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


async def _setup(session: AsyncSession, tag: str) -> tuple[str, str]:
    """A programme-isolated coach (es-speaking) + member (prefers es) with a Monday pattern."""
    programme_id = f"p-bk-{tag}"
    coach_id = f"c-bk-{tag}"
    member_id = f"m-bk-{tag}"
    from strata_core.domains.kernel import Programme

    if await session.get(Programme, programme_id) is None:
        session.add(Programme(id=programme_id, name=f"Booking test {tag}"))
        session.add(Coach(id=coach_id, display_name="Es Coach", timezone="UTC", languages=["es"]))
        session.add(
            Member(
                id=member_id,
                display_name="Es Member",
                timezone="UTC",
                languages=["es", "en"],
                preferred_language="es",
            )
        )
        for kind, uid in (("coach", coach_id), ("member", member_id)):
            session.add(
                ProgrammeAssignment(
                    id=f"pa-bk-{tag}-{kind}", programme_id=programme_id, user_kind=kind, user_id=uid
                )
            )
        await session.flush()
        actor = ActingContext(
            coach_id=coach_id, acting_user_id=coach_id, on_behalf_of_coach_id=None
        )
        await create_pattern(
            session,
            actor,
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
    return coach_id, member_id


@pytest.mark.anyio
async def test_browse_scopes_to_programme_and_hides_non_available(session: AsyncSession) -> None:
    """Browsing scopes to the member's programme coaches and hides non-available slots."""
    coach_id, member_id = await _setup(session, "b1")
    slots = await browse_slots(session, member_id, language_match=False, **WINDOW)
    assert slots and all(s.coach_id == coach_id for s in slots)  # only the programme's coach

    first = await session.get(Slot, slots[0].slot_id)
    assert first is not None
    first.status = "unavailable"
    await session.commit()
    after = await browse_slots(session, member_id, language_match=False, **WINDOW)
    assert slots[0].slot_id not in [s.slot_id for s in after]


@pytest.mark.anyio
async def test_language_toggle_filters_on_preferred_language(session: AsyncSession) -> None:
    """The language-match toggle filters slots to coaches speaking the member's language."""
    coach_id, member_id = await _setup(session, "b2")
    # Add an English-only coach to the same programme.
    session.add(Coach(id="c-bk-b2-en", display_name="En Coach", timezone="UTC", languages=["en"]))
    session.add(
        ProgrammeAssignment(
            id="pa-bk-b2-en", programme_id="p-bk-b2", user_kind="coach", user_id="c-bk-b2-en"
        )
    )
    await session.commit()

    toggled_off = await browse_slots(session, member_id, language_match=False, **WINDOW)
    toggled_on = await browse_slots(session, member_id, language_match=True, **WINDOW)
    # Off: all programme coaches; on: only the es-speaking coach (member prefers es).
    assert {s.coach_id for s in toggled_on} == {coach_id}
    assert {s.coach_id for s in toggled_off} >= {coach_id}


@pytest.mark.anyio
async def test_book_is_idempotent_on_member_slot_key(session: AsyncSession) -> None:
    """Booking is idempotent on the member+slot key: the replay returns the same record."""
    coach_id, member_id = await _setup(session, "b3")
    target = (await browse_slots(session, member_id, language_match=True, **WINDOW))[0]
    principal = as_principal(member_id, "member")

    first, replayed_first = await book(
        session,
        principal,
        slot_id=target.slot_id,
        member_id=member_id,
        language_matched=True,
        created_by="member",
        on_behalf_of_coach_id=None,
    )
    second, replayed_second = await book(
        session,
        principal,
        slot_id=target.slot_id,
        member_id=member_id,
        language_matched=True,
        created_by="member",
        on_behalf_of_coach_id=None,
    )
    assert (replayed_first, replayed_second) == (False, True)
    assert first.id == second.id

    slot = await session.get(Slot, target.slot_id)
    assert slot is not None and slot.status == "booked"
    assert first.booking_language_matched is True

    reminders = (
        (await session.execute(select(Reminder).where(Reminder.appointment_id == first.id)))
        .scalars()
        .all()
    )
    assert {r.recipient_kind for r in reminders} == {"member", "coach"}
    from datetime import timedelta

    assert all(r.due_at_utc == slot.start_utc - timedelta(hours=24) for r in reminders)
    assert all(r.status == "pending" for r in reminders)

    events = (
        (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == "AppointmentBooked")
            )
        )
        .scalars()
        .all()
    )
    assert sum(1 for e in events if e.payload.get("appointment_id") == first.id) == 1  # no dupe

    notes = (
        (await session.execute(select(Notification).where(Notification.recipient_id == member_id)))
        .scalars()
        .all()
    )
    assert {n.channel for n in notes} == {"email", "push"}


@pytest.mark.anyio
async def test_rebook_after_cancel_creates_a_fresh_appointment(session: AsyncSession) -> None:
    """Cancelling frees the member+slot key, so the same member can rebook the slot."""
    coach_id, member_id = await _setup(session, "b-rebook")
    target = (await browse_slots(session, member_id, language_match=True, **WINDOW))[0]
    principal = as_principal(member_id, "member")
    book_args = {
        "slot_id": target.slot_id,
        "member_id": member_id,
        "language_matched": True,
        "created_by": "member",
        "on_behalf_of_coach_id": None,
    }

    first, replayed_first = await book(session, principal, **book_args)
    assert replayed_first is False

    appointment = await session.get(Appointment, first.id)
    assert appointment is not None
    await cancel_appointment(
        session, principal, appointment, reason="changed plans", on_behalf_of_coach_id=None
    )
    freed = await session.get(Slot, target.slot_id)
    assert freed is not None and freed.status == "available"  # the slot is released

    # Re-booking the same member into the same slot must create a NEW appointment, not
    # replay the cancelled one — the cancel released the idempotency key.
    second, replayed_second = await book(session, principal, **book_args)
    assert replayed_second is False
    assert second.id != first.id
    rebooked = await session.get(Slot, target.slot_id)
    assert rebooked is not None and rebooked.status == "booked"


@pytest.mark.anyio
async def test_booked_slot_conflicts_for_other_member(session: AsyncSession) -> None:
    """A slot already booked by one member conflicts (409) for another."""
    coach_id, member_id = await _setup(session, "b4")
    session.add(
        Member(
            id="m-bk-b4-2",
            display_name="Second",
            timezone="UTC",
            languages=["es"],
            preferred_language="es",
        )
    )
    session.add(
        ProgrammeAssignment(
            id="pa-bk-b4-m2", programme_id="p-bk-b4", user_kind="member", user_id="m-bk-b4-2"
        )
    )
    await session.commit()
    target = (await browse_slots(session, member_id, language_match=False, **WINDOW))[0]

    await book(
        session,
        as_principal(member_id, "member"),
        slot_id=target.slot_id,
        member_id=member_id,
        language_matched=False,
        created_by="member",
        on_behalf_of_coach_id=None,
    )
    with pytest.raises(HTTPException) as exc:
        await book(
            session,
            as_principal("m-bk-b4-2", "member"),
            slot_id=target.slot_id,
            member_id="m-bk-b4-2",
            language_matched=False,
            created_by="member",
            on_behalf_of_coach_id=None,
        )
    assert exc.value.status_code == 409


@pytest.mark.anyio
async def test_book_desynced_slot_degrades_to_409(session: AsyncSession) -> None:
    """Booking a desynced slot hiding a confirmed appointment is 409, not 500."""
    coach_id, member_id = await _setup(session, "b-desync")
    session.add(
        Member(
            id="m-bk-b-desync-2",
            display_name="Second",
            timezone="UTC",
            languages=["es"],
            preferred_language="es",
        )
    )
    session.add(
        ProgrammeAssignment(
            id="pa-bk-b-desync-m2",
            programme_id="p-bk-b-desync",
            user_kind="member",
            user_id="m-bk-b-desync-2",
        )
    )
    await session.commit()
    target = (await browse_slots(session, member_id, language_match=False, **WINDOW))[0]
    first, _ = await book(
        session,
        as_principal(member_id, "member"),
        slot_id=target.slot_id,
        member_id=member_id,
        language_matched=False,
        created_by="member",
        on_behalf_of_coach_id=None,
    )

    # Stage the desync: the denormalized status lies while the confirmed appointment stands.
    slot = await session.get(Slot, target.slot_id)
    assert slot is not None
    slot.status = "available"
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await book(
            session,
            as_principal("m-bk-b-desync-2", "member"),
            slot_id=target.slot_id,
            member_id="m-bk-b-desync-2",
            language_matched=False,
            created_by="member",
            on_behalf_of_coach_id=None,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "Slot is no longer available"
    # The unique index won: the standing appointment was never superseded.
    standing = await session.get(Appointment, first.id)
    assert standing is not None and standing.status == "confirmed"


@pytest.mark.anyio
async def test_booking_outside_programme_is_403(session: AsyncSession) -> None:
    """A member cannot book a coach outside their programme (403)."""
    coach_id, _ = await _setup(session, "b5")
    # A member with no programme in common with the coach.
    session.add(
        Member(
            id="m-bk-b5-x",
            display_name="Outsider",
            timezone="UTC",
            languages=["en"],
            preferred_language="en",
        )
    )
    await session.flush()
    slot = (
        (
            await session.execute(
                select(Slot).where(Slot.coach_id == coach_id, Slot.status == "available")
            )
        )
        .scalars()
        .first()
    )
    assert slot is not None
    with pytest.raises(HTTPException) as exc:
        await book(
            session,
            as_principal("m-bk-b5-x", "member"),
            slot_id=slot.id,
            member_id="m-bk-b5-x",
            language_matched=False,
            created_by="member",
            on_behalf_of_coach_id=None,
        )
    assert exc.value.status_code == 403
    await session.rollback()
