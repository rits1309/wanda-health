"""Admin capability tests: combined calendar + act-for-coach attribution."""

from datetime import UTC, date, datetime, time

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import Appointment, Notification, OutboxEvent
from strata_core.domains.kernel import Programme

from strata_booking.core import clock as clock_module
from strata_booking.schemas.availability import PatternBody
from strata_booking.services import calendar, rescheduling
from strata_booking.services.authz import ActingContext
from strata_booking.services.availability import create_pattern
from strata_booking.services.booking import book, browse_slots
from tests.auth import as_principal
from tests.identity_stubs import Admin, Coach, Member, ProgrammeAssignment

NOW = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
WINDOW = {"from_date": date(2026, 10, 1), "to_date": date(2026, 10, 31)}


@pytest.fixture(autouse=True)
def _pin_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock_module.clock.__class__, "now", lambda self: NOW)


async def _fixture(session: AsyncSession) -> Appointment:
    """Two programmes: the admin administers only the first. One booked slot on coach 1."""
    if await session.get(Programme, "p-adm-1") is None:
        session.add(Programme(id="p-adm-1", name="Adm 1"))
        session.add(Programme(id="p-adm-2", name="Adm 2"))
        session.add(
            Member(
                id="m-adm-1",
                display_name="M",
                timezone="UTC",
                languages=["en"],
                preferred_language="en",
            )
        )
        # The admin is a kernel row too: admin reach derives from
        # programme_assignments, no longer from token claims.
        session.add(Admin(id="a-adm"))
        rows = [
            ("p-adm-1", "member", "m-adm-1"),
            ("p-adm-1", "coach", "c-adm-1"),
            ("p-adm-1", "admin", "a-adm"),
            ("p-adm-2", "coach", "c-adm-2"),
        ]
        for i in (1, 2):
            session.add(
                Coach(id=f"c-adm-{i}", display_name=f"C{i}", timezone="UTC", languages=["en"])
            )
        for programme, kind, uid in rows:
            session.add(
                ProgrammeAssignment(
                    id=f"pa-adm-{programme}-{uid}",
                    programme_id=programme,
                    user_kind=kind,
                    user_id=uid,
                )
            )
        await session.flush()
        for i in (1, 2):
            await create_pattern(
                session,
                ActingContext(
                    coach_id=f"c-adm-{i}",
                    acting_user_id=f"c-adm-{i}",
                    on_behalf_of_coach_id=None,
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
        slots = await browse_slots(session, "m-adm-1", language_match=False, **WINDOW)
        await book(
            session,
            as_principal("m-adm-1", "member"),
            slot_id=slots[0].slot_id,
            member_id="m-adm-1",
            language_matched=False,
            created_by="member",
            on_behalf_of_coach_id=None,
        )
    appointment = (
        await session.execute(select(Appointment).where(Appointment.member_id == "m-adm-1"))
    ).scalar_one()
    return appointment


ADMIN = as_principal("a-adm", "admin")


@pytest.mark.anyio
async def test_admin_calendar_defaults_to_administered_coaches(session: AsyncSession) -> None:
    """The admin calendar defaults to the admin's programme coaches with all layers."""
    await _fixture(session)
    out = await calendar.admin_calendar(session, ADMIN, coach_ids=None, layers=None, **WINDOW)
    assert [c.coach_id for c in out.coaches] == ["c-adm-1"]  # never the p-adm-2 coach
    assert out.layers == ["available", "booked", "unavailable"]
    statuses = {e.status for c in out.coaches for e in c.entries}
    assert "booked" in statuses and "available" in statuses


@pytest.mark.anyio
async def test_admin_calendar_layer_toggle(session: AsyncSession) -> None:
    """The layers filter narrows the admin calendar to just the requested layer."""
    await _fixture(session)
    out = await calendar.admin_calendar(
        session, ADMIN, coach_ids=["c-adm-1"], layers=["booked"], **WINDOW
    )
    entries = [e for c in out.coaches for e in c.entries]
    assert entries and all(e.status == "booked" for e in entries)
    assert all(e.appointment is not None for e in entries)


@pytest.mark.anyio
async def test_admin_calendar_rejects_coaches_outside_programmes(
    session: AsyncSession,
) -> None:
    """Requesting a coach outside the admin's programmes is 403 naming the coach."""
    await _fixture(session)
    with pytest.raises(HTTPException) as exc:
        await calendar.admin_calendar(session, ADMIN, coach_ids=["c-adm-2"], layers=None, **WINDOW)
    assert exc.value.status_code == 403
    assert "c-adm-2" in exc.value.detail


@pytest.mark.anyio
async def test_admin_actions_carry_on_behalf_attribution(session: AsyncSession) -> None:
    """Admin actions on a coach's behalf carry the on-behalf attribution in notifications."""
    appointment = await _fixture(session)
    await rescheduling.request_reschedule(
        session, ADMIN, appointment, on_behalf_of_coach_id=appointment.coach_id
    )
    note = (
        await session.execute(
            select(Notification).where(
                Notification.recipient_id == "m-adm-1",
                Notification.notification_type == "reschedule_requested",
            )
        )
    ).scalar_one()
    assert note.payload["on_behalf_of_coach_id"] == appointment.coach_id

    # And the booking made earlier by the member records no attribution.
    events = (
        (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == "AppointmentBooked")
            )
        )
        .scalars()
        .all()
    )
    mine = [e for e in events if e.payload.get("member_id") == "m-adm-1"]
    assert mine and mine[0].payload.get("on_behalf_of_coach_id") is None
