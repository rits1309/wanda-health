"""Policy resolution + cancellation flow unit tests."""

from datetime import UTC, date, datetime, time

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import CancellationPolicy, OutboxEvent, Reminder, Slot
from strata_core.domains.kernel import Programme, UserProfile

from strata_booking.core import clock as clock_module
from strata_booking.schemas.availability import PatternBody
from strata_booking.schemas.policy import PolicyBody
from strata_booking.services.authz import ActingContext
from strata_booking.services.availability import create_pattern
from strata_booking.services.booking import book, browse_slots
from strata_booking.services.cancellation import cancel_appointment
from strata_booking.services.policy import create_policy, effective_policy
from tests.auth import as_principal
from tests.identity_stubs import Admin, Coach, Member, ProgrammeAssignment

NOW = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
WINDOW = {"from_date": date(2026, 10, 1), "to_date": date(2026, 10, 31)}


@pytest.fixture(autouse=True)
def _pin_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock_module.clock.__class__, "now", lambda self: NOW)


async def _setup(session: AsyncSession, tag: str) -> tuple[str, str, str]:
    """Isolated programme + coach + member with a Monday pattern; returns their ids."""
    programme_id, coach_id, member_id = f"p-pc-{tag}", f"c-pc-{tag}", f"m-pc-{tag}"
    if await session.get(Programme, programme_id) is None:
        session.add(Programme(id=programme_id, name=f"Policy test {tag}"))
        session.add(Coach(id=coach_id, display_name="C", timezone="UTC", languages=["en"]))
        session.add(
            Member(
                id=member_id,
                display_name="M",
                timezone="UTC",
                languages=["en"],
                preferred_language="en",
            )
        )
        # a-pol administers every test programme (kernel-derived reach);
        # the profile row is shared across tags, so add it only once.
        if await session.get(UserProfile, "a-pol") is None:
            session.add(Admin(id="a-pol"))
        for kind, uid in (("coach", coach_id), ("member", member_id), ("admin", "a-pol")):
            session.add(
                ProgrammeAssignment(
                    id=f"pa-pc-{tag}-{kind}", programme_id=programme_id, user_kind=kind, user_id=uid
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
    return programme_id, coach_id, member_id


async def _booked(session: AsyncSession, tag: str, slot_index: int = 0) -> tuple[str, str, str]:
    programme_id, coach_id, member_id = await _setup(session, tag)
    target = (await browse_slots(session, member_id, language_match=False, **WINDOW))[slot_index]
    out, _ = await book(
        session,
        as_principal(member_id, "member"),
        slot_id=target.slot_id,
        member_id=member_id,
        language_matched=False,
        created_by="member",
        on_behalf_of_coach_id=None,
    )
    return out.id, coach_id, member_id


ADMIN = as_principal("a-pol", "admin")


@pytest.mark.anyio
async def test_effective_policy_precedence_and_default(session: AsyncSession) -> None:
    """Effective policy resolves coach over programme over the 24h/both default."""
    programme_id, coach_id, _ = await _setup(session, "e1")

    default = await effective_policy(session, programme_id, coach_id)
    assert (default.source, default.window_hours, default.allowed_by) == ("default", 24, "both")

    await create_policy(
        session,
        ADMIN,
        PolicyBody(
            scope="programme",
            programme_id=programme_id,
            cancellation_window_hours=12,
            cancellation_allowed_by="both",
        ),
    )
    programme_level = await effective_policy(session, programme_id, coach_id)
    assert (programme_level.source, programme_level.window_hours) == ("programme", 12)

    await create_policy(
        session,
        ADMIN,
        PolicyBody(
            scope="coach",
            programme_id=programme_id,
            coach_id=coach_id,
            cancellation_window_hours=48,
            cancellation_allowed_by="coach",
        ),
    )
    coach_level = await effective_policy(session, programme_id, coach_id)
    assert (coach_level.source, coach_level.window_hours, coach_level.allowed_by) == (
        "coach",
        48,
        "coach",
    )


@pytest.mark.anyio
async def test_policy_versions_are_immutable_appends(session: AsyncSession) -> None:
    """Policy writes append new versions; earlier versions stay immutable."""
    programme_id, _, _ = await _setup(session, "e1")
    admin = as_principal("a-pol", "admin")
    body = PolicyBody(
        scope="programme",
        programme_id=programme_id,
        cancellation_window_hours=6,
        cancellation_allowed_by="member",
    )
    await create_policy(session, admin, body)
    rows = (
        (
            await session.execute(
                select(CancellationPolicy).where(
                    CancellationPolicy.programme_id == programme_id,
                    CancellationPolicy.scope == "programme",
                )
            )
        )
        .scalars()
        .all()
    )
    versions = sorted((r.version, r.active) for r in rows)
    assert versions[-1][1] is True  # newest active
    assert all(not active for _, active in versions[:-1])  # all prior versions deactivated


@pytest.mark.anyio
async def test_member_cancel_inside_window_frees_slot(session: AsyncSession) -> None:
    """A member cancel outside the window succeeds and frees the slot."""
    appointment_id, coach_id, member_id = await _booked(session, "c1")
    from strata_core.domains.booking import Appointment

    appointment = await session.get(Appointment, appointment_id)
    assert appointment is not None
    await cancel_appointment(
        session,
        as_principal(member_id, "member"),
        appointment,
        reason="conflict",
        on_behalf_of_coach_id=None,
    )
    assert appointment.status == "cancelled"
    assert appointment.cancelled_by == "member"
    slot = await session.get(Slot, appointment.slot_id)
    assert slot is not None and slot.status == "available"
    reminders = (
        (await session.execute(select(Reminder).where(Reminder.appointment_id == appointment_id)))
        .scalars()
        .all()
    )
    assert reminders and all(r.status == "cancelled" for r in reminders)
    events = (
        (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == "AppointmentCancelled")
            )
        )
        .scalars()
        .all()
    )
    assert any(e.payload.get("appointment_id") == appointment_id for e in events)


@pytest.mark.anyio
async def test_cancel_outside_window_is_409(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancel inside the policy window is refused with 409."""
    appointment_id, coach_id, member_id = await _booked(session, "c2")
    from strata_core.domains.booking import Appointment

    appointment = await session.get(Appointment, appointment_id)
    assert appointment is not None
    slot = await session.get(Slot, appointment.slot_id)
    assert slot is not None
    # Move "now" to 1 hour before the appointment — inside the default 24h window boundary.
    monkeypatch.setattr(
        clock_module.clock.__class__,
        "now",
        lambda self: slot.start_utc.replace(hour=slot.start_utc.hour - 1),
    )
    with pytest.raises(HTTPException) as exc:
        await cancel_appointment(
            session,
            as_principal(member_id, "member"),
            appointment,
            reason=None,
            on_behalf_of_coach_id=None,
        )
    assert exc.value.status_code == 409
    assert "window" in exc.value.detail.lower()
    await session.rollback()


@pytest.mark.anyio
async def test_policy_allowed_by_blocks_member(session: AsyncSession) -> None:
    """A coach-only cancellation policy blocks the member from cancelling."""
    appointment_id, coach_id, member_id = await _booked(session, "c3")
    programme_id = "p-pc-c3"
    admin = as_principal("a-pol", "admin")
    await create_policy(
        session,
        admin,
        PolicyBody(
            scope="programme",
            programme_id=programme_id,
            cancellation_window_hours=0,
            cancellation_allowed_by="coach",
        ),
    )
    from strata_core.domains.booking import Appointment

    appointment = await session.get(Appointment, appointment_id)
    assert appointment is not None
    with pytest.raises(HTTPException) as exc:
        await cancel_appointment(
            session,
            as_principal(member_id, "member"),
            appointment,
            reason=None,
            on_behalf_of_coach_id=None,
        )
    assert exc.value.status_code == 403
    await session.rollback()

    # The coach can cancel; the member gets the notification with the booking link.
    appointment = await session.get(Appointment, appointment_id)  # re-fetch after rollback
    assert appointment is not None
    await cancel_appointment(
        session,
        as_principal(coach_id, "coach"),
        appointment,
        reason="emergency",
        on_behalf_of_coach_id=None,
    )
    from strata_core.domains.booking import Notification

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
    assert any(n.payload.get("appointment_id") == appointment_id for n in notes)
    assert all("booking_page_link" in n.payload for n in notes)
