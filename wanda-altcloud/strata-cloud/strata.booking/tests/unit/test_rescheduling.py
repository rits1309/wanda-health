"""Rescheduling engine unit tests: swap, chain, window, request, history."""

from datetime import UTC, date, datetime, time, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import (
    Appointment,
    Notification,
    OutboxEvent,
    Reminder,
    RescheduleEvent,
    RescheduleToken,
    Slot,
)
from strata_core.domains.kernel import Programme

from strata_booking.core import clock as clock_module
from strata_booking.core.security import Principal
from strata_booking.schemas.availability import PatternBody
from strata_booking.services import rescheduling
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
    """Programme with two coaches (second es-only) on a Monday 9–10 pattern + an en member."""
    programme_id, member_id = f"p-rs-{tag}", f"m-rs-{tag}"
    if await session.get(Programme, programme_id) is None:
        session.add(Programme(id=programme_id, name=f"Rs {tag}"))
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
                id=f"pa-rs-{tag}-m",
                programme_id=programme_id,
                user_kind="member",
                user_id=member_id,
            )
        )
        for i, languages in ((1, ["en"]), (2, ["es"])):
            coach_id = f"c-rs-{tag}-{i}"
            session.add(
                Coach(id=coach_id, display_name=f"Coach {i}", timezone="UTC", languages=languages)
            )
            session.add(
                ProgrammeAssignment(
                    id=f"pa-rs-{tag}-{i}",
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
    return member_id


async def _book_first_slot(
    session: AsyncSession, tag: str, *, language_matched: bool = False
) -> Appointment:
    member_id = await _fixture(session, tag)
    slots = await browse_slots(
        session, member_id, language_match=False, coach_id=f"c-rs-{tag}-1", **WINDOW
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


def _member(appointment: Appointment) -> Principal:
    return as_principal(appointment.member_id, "member")


async def _later_slot(session: AsyncSession, appointment: Appointment) -> Slot:
    """Another available slot of the same coach, after the booked one."""
    slot = (
        (
            await session.execute(
                select(Slot)
                .where(
                    Slot.coach_id == appointment.coach_id,
                    Slot.status == "available",
                    Slot.id != appointment.slot_id,
                )
                .order_by(Slot.start_utc)
            )
        )
        .scalars()
        .first()
    )
    assert slot is not None
    return slot


@pytest.mark.anyio
async def test_reschedule_swaps_slots_and_links_the_chain(session: AsyncSession) -> None:
    """Rescheduling frees the old slot, books the new one and links the chain."""
    appointment = await _book_first_slot(session, "s1")
    old_slot_id = appointment.slot_id
    target = await _later_slot(session, appointment)

    out = await rescheduling.reschedule_appointment(
        session, _member(appointment), appointment, new_slot_id=target.id
    )

    successor = await session.get(Appointment, out.id)
    assert successor is not None and successor.status == "confirmed"
    assert successor.original_appointment_id == appointment.id
    assert out.member_name == "M"  # the kernel name, not the id
    assert appointment.status == "rescheduled"
    old_slot = await session.get(Slot, old_slot_id)
    new_slot = await session.get(Slot, target.id)
    assert old_slot is not None and old_slot.status == "available"
    assert new_slot is not None and new_slot.status == "booked"

    hop = (
        await session.execute(
            select(RescheduleEvent).where(RescheduleEvent.from_appointment_id == appointment.id)
        )
    ).scalar_one()
    assert hop.to_appointment_id == successor.id
    assert hop.from_slot_id == old_slot_id and hop.to_slot_id == target.id
    assert hop.initiated_by == "member"

    # Reminders swapped over: old cancelled, successor pending.
    old_reminders = (
        (await session.execute(select(Reminder).where(Reminder.appointment_id == appointment.id)))
        .scalars()
        .all()
    )
    assert old_reminders and all(r.status == "cancelled" for r in old_reminders)
    new_reminders = (
        (await session.execute(select(Reminder).where(Reminder.appointment_id == successor.id)))
        .scalars()
        .all()
    )
    assert {r.recipient_kind for r in new_reminders} == {"member", "coach"}
    assert all(r.status == "pending" for r in new_reminders)

    events = (
        (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == "AppointmentRescheduled")
            )
        )
        .scalars()
        .all()
    )
    assert any(e.payload.get("to_appointment_id") == successor.id for e in events)

    # Both parties notified.
    all_notes = (
        (
            await session.execute(
                select(Notification).where(
                    Notification.notification_type == "appointment_rescheduled"
                )
            )
        )
        .scalars()
        .all()
    )
    notes = [n for n in all_notes if n.payload.get("appointment_id") == successor.id]
    assert {(n.recipient_kind, n.recipient_id) for n in notes} == {
        ("member", appointment.member_id),
        ("coach", appointment.coach_id),
    }


@pytest.mark.anyio
async def test_reschedule_inside_window_is_409(session: AsyncSession) -> None:
    """A reschedule inside the policy window is refused with 409."""
    appointment = await _book_first_slot(session, "w1")
    target = await _later_slot(session, appointment)
    slot = await session.get(Slot, appointment.slot_id)
    assert slot is not None
    inside = slot.start_utc - timedelta(hours=12)  # default policy window is 24h
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(clock_module.clock.__class__, "now", lambda self: inside)
        with pytest.raises(HTTPException) as exc:
            await rescheduling.reschedule_appointment(
                session, _member(appointment), appointment, new_slot_id=target.id
            )
    assert exc.value.status_code == 409
    assert "window" in exc.value.detail.lower()


@pytest.mark.anyio
async def test_reschedule_to_taken_slot_is_409_and_nothing_moves(session: AsyncSession) -> None:
    """Rescheduling onto a taken slot is 409 and nothing moves."""
    appointment = await _book_first_slot(session, "t1")
    appointment_id = appointment.id
    target = await _later_slot(session, appointment)
    target_id = target.id
    target.status = "booked"
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await rescheduling.reschedule_appointment(
            session, _member(appointment), appointment, new_slot_id=target_id
        )
    assert exc.value.status_code == 409
    await session.rollback()
    session.expunge_all()  # drop expired instances so nothing refreshes lazily
    status = (
        await session.execute(select(Appointment.status).where(Appointment.id == appointment_id))
    ).scalar_one()
    assert status == "confirmed"


@pytest.mark.anyio
async def test_reschedule_to_desynced_slot_degrades_to_409(session: AsyncSession) -> None:
    """Rescheduling onto a desynced slot hiding a confirmed appointment is 409."""
    appointment = await _book_first_slot(session, "d1")
    appointment_id = appointment.id
    target = await _later_slot(session, appointment)
    target_id = target.id

    # A rival member holds the target slot; then the denormalized status lies available.
    session.add(
        Member(
            id="m-rs-d1-2",
            display_name="Rival",
            timezone="UTC",
            languages=["en"],
            preferred_language="en",
        )
    )
    session.add(
        ProgrammeAssignment(
            id="pa-rs-d1-m2", programme_id="p-rs-d1", user_kind="member", user_id="m-rs-d1-2"
        )
    )
    await session.commit()
    await book(
        session,
        as_principal("m-rs-d1-2", "member"),
        slot_id=target_id,
        member_id="m-rs-d1-2",
        language_matched=False,
        created_by="member",
        on_behalf_of_coach_id=None,
    )
    desynced = await session.get(Slot, target_id)
    assert desynced is not None
    desynced.status = "available"
    await session.commit()

    with pytest.raises(HTTPException) as exc:
        await rescheduling.reschedule_appointment(
            session, _member(appointment), appointment, new_slot_id=target_id
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "Slot is no longer available"
    await session.rollback()
    session.expunge_all()  # drop expired instances so nothing refreshes lazily
    status = (
        await session.execute(select(Appointment.status).where(Appointment.id == appointment_id))
    ).scalar_one()
    assert status == "confirmed"  # nothing moved: the original booking stands


@pytest.mark.anyio
async def test_language_matched_reschedule_rejects_non_matching_coach(
    session: AsyncSession,
) -> None:
    """A language-matched booking cannot reschedule onto a non-matching coach."""
    appointment = await _book_first_slot(session, "l1", language_matched=True)
    es_slot = (
        (
            await session.execute(
                select(Slot)
                .where(Slot.coach_id == "c-rs-l1-2", Slot.status == "available")
                .order_by(Slot.start_utc)
            )
        )
        .scalars()
        .first()
    )
    assert es_slot is not None

    with pytest.raises(HTTPException) as exc:
        await rescheduling.reschedule_appointment(
            session, _member(appointment), appointment, new_slot_id=es_slot.id
        )
    assert exc.value.status_code == 422


@pytest.mark.anyio
async def test_cancelled_appointment_is_not_reschedulable(session: AsyncSession) -> None:
    """A cancelled appointment cannot be rescheduled."""
    appointment = await _book_first_slot(session, "c1")
    target = await _later_slot(session, appointment)
    await cancel_appointment(
        session, _member(appointment), appointment, reason=None, on_behalf_of_coach_id=None
    )
    with pytest.raises(HTTPException) as exc:
        await rescheduling.reschedule_appointment(
            session, _member(appointment), appointment, new_slot_id=target.id
        )
    assert exc.value.status_code == 409


@pytest.mark.anyio
async def test_request_reschedule_mints_link_and_fulfilment_spends_it(
    session: AsyncSession,
) -> None:
    """Requesting a reschedule mints a link the fulfilment then spends."""
    appointment = await _book_first_slot(session, "r1")
    coach = as_principal(appointment.coach_id, "coach")

    out = await rescheduling.request_reschedule(
        session, coach, appointment, on_behalf_of_coach_id=None
    )
    assert out.expires_at == NOW + timedelta(days=7)  # programme default TTL

    token_row = (
        await session.execute(
            select(RescheduleToken).where(RescheduleToken.appointment_id == appointment.id)
        )
    ).scalar_one()
    assert token_row.audience == "member" and token_row.used_at is None

    note = (
        await session.execute(
            select(Notification).where(
                Notification.recipient_id == appointment.member_id,
                Notification.notification_type == "reschedule_requested",
            )
        )
    ).scalar_one()
    assert "/reschedule?token=" in str(note.payload["link"])
    assert note.payload["booking_page_link"]

    # Member fulfils → the open link is spent.
    target = await _later_slot(session, appointment)
    await rescheduling.reschedule_appointment(
        session, _member(appointment), appointment, new_slot_id=target.id
    )
    await session.refresh(token_row)
    assert token_row.used_at is not None


@pytest.mark.anyio
async def test_history_returns_the_whole_chain_from_any_record(session: AsyncSession) -> None:
    """History returns the whole reschedule chain from any record in it."""
    first = await _book_first_slot(session, "h1")
    second_slot = await _later_slot(session, first)
    second_out = await rescheduling.reschedule_appointment(
        session, _member(first), first, new_slot_id=second_slot.id
    )
    second = await session.get(Appointment, second_out.id)
    assert second is not None
    third_slot = await _later_slot(session, second)
    third_out = await rescheduling.reschedule_appointment(
        session, _member(second), second, new_slot_id=third_slot.id
    )

    for record in (first, second):
        chain = await rescheduling.history(session, record)
        assert [a.id for a in chain.appointments] == [first.id, second.id, third_out.id]
        # Every chain record carries the kernel name, never the raw id.
        assert {a.member_name for a in chain.appointments} == {"M"}
        assert [a.status for a in chain.appointments] == [
            "rescheduled",
            "rescheduled",
            "confirmed",
        ]
        assert len(chain.reschedules) == 2
        assert chain.reschedules[0].from_appointment_id == first.id
        assert chain.reschedules[1].to_appointment_id == third_out.id
