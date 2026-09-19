"""Availability service unit tests: versioning, regeneration, orphan cancellation, impact."""

from datetime import date, datetime, time, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import (
    Appointment,
    AvailabilityPatternVersion,
    Notification,
    OutboxEvent,
    Slot,
    SuggestedAlternative,
)
from strata_core.domains.kernel import UserProfile

from strata_booking.core import clock as clock_module
from strata_booking.schemas.availability import PatternBody
from strata_booking.services import alternatives
from strata_booking.services.authz import ActingContext
from strata_booking.services.availability import (
    create_pattern,
    delete_pattern,
    pattern_impact,
    update_pattern,
)
from strata_booking.services.booking import book
from strata_booking.services.calendar import coach_calendar
from strata_booking.services.cancellation import cancel_appointment
from strata_booking.services.tokens import hash_token, mint_link_token
from tests.auth import as_principal
from tests.identity_stubs import Coach, ProgrammeAssignment

# All service tests pin "now" so horizons and future-slot boundaries are deterministic.
NOW = datetime(2026, 10, 1, 8, 0, tzinfo=__import__("datetime").UTC)


@pytest.fixture(autouse=True)
def _pin_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock_module.clock.__class__, "now", lambda self: NOW)


def _body(**overrides: object) -> PatternBody:
    defaults: dict[str, object] = {
        "days_of_week": [0],  # Mondays
        "start_time_local": time(9, 0),
        "end_time_local": time(10, 0),
        "slot_duration_minutes": 20,
        "timezone": "America/New_York",
        "active_from": date(2026, 10, 1),
        "active_to": date(2026, 12, 31),
    }
    defaults.update(overrides)
    return PatternBody(**defaults)  # type: ignore[arg-type]


async def _coach(session: AsyncSession, coach_id: str) -> ActingContext:
    if await session.get(UserProfile, coach_id) is None:
        session.add(Coach(id=coach_id, display_name="Svc", timezone="UTC", languages=["en"]))
        session.add(
            ProgrammeAssignment(
                id=f"pa-{coach_id}", programme_id="p-1", user_kind="coach", user_id=coach_id
            )
        )
        await session.flush()
    return ActingContext(coach_id=coach_id, acting_user_id=coach_id, on_behalf_of_coach_id=None)


async def _events(session: AsyncSession, event_type: str, pattern_id: str) -> list[OutboxEvent]:
    rows = (
        (await session.execute(select(OutboxEvent).where(OutboxEvent.event_type == event_type)))
        .scalars()
        .all()
    )
    return [r for r in rows if r.payload.get("pattern_id") == pattern_id]


@pytest.mark.anyio
async def test_create_generates_slots_snapshots_and_emits(session: AsyncSession) -> None:
    """Creating a pattern generates its slots, snapshots the version and emits the event."""
    actor = await _coach(session, "c-svc1")
    pattern = await create_pattern(session, actor, _body())

    slots = (
        await session.execute(
            select(func.count()).select_from(Slot).where(Slot.pattern_id == pattern.id)
        )
    ).scalar_one()
    assert slots > 0  # Mondays within the 4-week horizon
    versions = (
        (
            await session.execute(
                select(AvailabilityPatternVersion).where(
                    AvailabilityPatternVersion.pattern_id == pattern.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert [v.version for v in versions] == [1]
    assert len(await _events(session, "AvailabilityPatternCreated", pattern.id)) == 1
    assert len(await _events(session, "SlotsGenerated", pattern.id)) == 1


@pytest.mark.anyio
async def test_reshape_after_a_freed_booking_retires_the_slot(session: AsyncSession) -> None:
    """Reshaping over a booked-then-cancelled slot retires it — never deletes it.

    The cancelled appointment row keeps its ``slot_id`` (the slot carries the "when" of
    history), so the slot row must outlive the reshape.
    """
    actor = await _coach(session, "c-svc-fk1")
    pattern = await create_pattern(session, actor, _body())
    slots = (
        (
            await session.execute(
                select(Slot).where(Slot.pattern_id == pattern.id).order_by(Slot.start_utc)
            )
        )
        .scalars()
        .all()
    )
    freed = slots[1]  # 9:20 — booked, cancelled (freed), then dropped by the reshape below
    out, _ = await book(
        session,
        as_principal("m-1", "member"),
        slot_id=freed.id,
        member_id="m-1",
        language_matched=False,
        created_by="member",
        on_behalf_of_coach_id=None,
    )
    appointment = await session.get(Appointment, out.id)
    assert appointment is not None
    await cancel_appointment(
        session, as_principal("m-1", "member"), appointment, reason=None, on_behalf_of_coach_id=None
    )
    assert freed.status == "available"  # freed for rebooking

    freed_id, appointment_id = freed.id, out.id
    await update_pattern(session, actor, pattern, _body(end_time_local=time(9, 20)))

    session.expire_all()  # read back what the reshape actually persisted
    retired = await session.get(Slot, freed_id)
    assert retired is not None and retired.status == "cancelled"  # retired, not deleted
    history = await session.get(Appointment, appointment_id)
    assert history is not None and history.slot_id == freed_id  # history keeps its "when"


@pytest.mark.anyio
async def test_reshape_over_desynced_booked_slot_observes_the_desync(
    session: AsyncSession,
) -> None:
    """A reshape that retires a booked slot with no appointment at all observes it.

    Same desync family as the unavailability case, at the regeneration touchpoint: a
    booked-status slot with no confirmed appointment falls into the retire bucket (correct —
    nothing real is dropped), but the lie must be recorded, not silently absorbed: the retire
    still happens and a ``SlotDesyncObserved`` event marks it.
    """
    actor = await _coach(session, "c-svc-dsy")
    pattern = await create_pattern(session, actor, _body())
    slots = (
        (
            await session.execute(
                select(Slot).where(Slot.pattern_id == pattern.id).order_by(Slot.start_utc)
            )
        )
        .scalars()
        .all()
    )
    liar = slots[1]  # 9:20 — booked status, yet no appointment row exists anywhere
    liar.status = "booked"
    await session.commit()
    liar_id = liar.id

    await update_pattern(session, actor, pattern, _body(end_time_local=time(9, 20)))

    session.expire_all()
    retired = await session.get(Slot, liar_id)
    assert retired is not None and retired.status == "cancelled"  # still retired, not deleted
    desync = (
        (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == "SlotDesyncObserved")
            )
        )
        .scalars()
        .all()
    )
    assert [e.payload.get("slot_id") for e in desync] == [liar_id]
    assert desync[0].payload.get("healed_to") == "cancelled"


@pytest.mark.anyio
async def test_desync_observation_reports_the_post_regeneration_state(
    session: AsyncSession,
) -> None:
    """When regeneration revives a desynced slot, the event says so — never "cancelled".

    A reshape that keeps the slot's exact window retires it and then revives it in the same
    call (the idempotent upsert), so the slot's real outcome is ``available``. The
    ``SlotDesyncObserved`` audit trail must report that database truth, not the intermediate
    retire.
    """
    actor = await _coach(session, "c-svc-dsy2")
    pattern = await create_pattern(session, actor, _body())
    slots = (
        (
            await session.execute(
                select(Slot).where(Slot.pattern_id == pattern.id).order_by(Slot.start_utc)
            )
        )
        .scalars()
        .all()
    )
    liar = slots[1]  # 9:20 — booked status, no appointment; its window survives the reshape
    liar.status = "booked"
    await session.commit()
    liar_id = liar.id

    await update_pattern(session, actor, pattern, _body(days_of_week=[0, 1]))  # add Tuesdays

    session.expire_all()
    revived = await session.get(Slot, liar_id)
    assert revived is not None and revived.status == "available"  # retired, then revived
    desync = (
        (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == "SlotDesyncObserved")
            )
        )
        .scalars()
        .all()
    )
    assert [e.payload.get("slot_id") for e in desync] == [liar_id]
    assert desync[0].payload.get("healed_to") == "available"


@pytest.mark.anyio
async def test_reshape_with_live_suggestion_reference_retires_the_slot(
    session: AsyncSession,
) -> None:
    """Reshaping over a slot a suggested alternative references retires it, and the
    suggestion degrades to the graceful slot-taken path."""
    actor = await _coach(session, "c-svc-fk2")
    pattern = await create_pattern(session, actor, _body())
    slots = (
        (
            await session.execute(
                select(Slot).where(Slot.pattern_id == pattern.id).order_by(Slot.start_utc)
            )
        )
        .scalars()
        .all()
    )
    anchor, referenced = slots[0], slots[1]  # 9:00 booked anchor; 9:20 available + referenced
    anchor.status = "booked"
    session.add(
        Appointment(
            id="appt-svc-fk2",
            slot_id=anchor.id,
            coach_id="c-svc-fk2",
            member_id="m-1",
            programme_id="p-1",
            created_by="member",
            acting_user_id="m-1",
            idempotency_key=f"m-1:{anchor.id}",
        )
    )
    # The link an earlier coach-cancellation leaves behind: a live suggestion
    # pointing at the pattern's still-available 9:20 slot.
    token = mint_link_token("alternative", "alt-svc-fk2", timedelta(days=7))
    session.add(
        SuggestedAlternative(
            id="alt-svc-fk2",
            cancelled_appointment_id="appt-svc-fk2",
            rank=1,
            kind="same_day_window",
            slot_id=referenced.id,
            token_hash=hash_token(token),
        )
    )
    await session.commit()

    referenced_id = referenced.id
    await update_pattern(session, actor, pattern, _body(end_time_local=time(9, 20)))

    session.expire_all()
    retired = await session.get(Slot, referenced_id)
    assert retired is not None and retired.status == "cancelled"
    # The suggestion survives and degrades exactly like a taken slot.
    with pytest.raises(HTTPException) as exc:
        await alternatives.accept(session, token)
    assert exc.value.status_code == 409
    assert "booking page" in exc.value.detail
    link = await session.get(SuggestedAlternative, "alt-svc-fk2")
    assert link is not None and link.status == "slot_taken"


@pytest.mark.anyio
async def test_rewidening_revives_retired_slots(session: AsyncSession) -> None:
    """Re-widening a pattern revives retired slots at re-covered times — no holes."""
    actor = await _coach(session, "c-svc-fk3")
    pattern = await create_pattern(session, actor, _body())
    before = (
        (
            await session.execute(
                select(Slot).where(Slot.pattern_id == pattern.id).order_by(Slot.start_utc)
            )
        )
        .scalars()
        .all()
    )
    dropped_ids = [s.id for s in before[1:]]  # 9:20 and 9:40 go when narrowing to 9:00–9:20

    await update_pattern(session, actor, pattern, _body(end_time_local=time(9, 20)))
    await update_pattern(session, actor, pattern, _body())  # back to the original 9:00–10:00

    pattern_id, expected_version = pattern.id, pattern.version
    # The revive is a Core upsert: it updates rows, not in-session objects — expire so
    # the reads below see what was persisted (request sessions are always fresh).
    session.expire_all()
    after = (
        (
            await session.execute(
                select(Slot).where(Slot.pattern_id == pattern_id, Slot.status == "available")
            )
        )
        .scalars()
        .all()
    )
    assert len(after) == len(before)  # the full shape is offered again — no holes
    revived_ids = {s.id for s in after}
    assert set(dropped_ids) <= revived_ids  # the SAME rows came back, not duplicates
    versions = {s.pattern_version for s in after if s.id in dropped_ids}
    assert versions == {expected_version}


@pytest.mark.anyio
async def test_calendar_hides_retired_slots(session: AsyncSession) -> None:
    """Retired slots never reach calendar payloads — only declared layers do."""
    actor = await _coach(session, "c-svc-fk4")
    pattern = await create_pattern(session, actor, _body())
    slots = (
        (
            await session.execute(
                select(Slot).where(Slot.pattern_id == pattern.id).order_by(Slot.start_utc)
            )
        )
        .scalars()
        .all()
    )
    kept_id, retired_ids = slots[0].id, {slots[1].id, slots[2].id}
    await update_pattern(session, actor, pattern, _body(end_time_local=time(9, 20)))

    # The revive upsert bypasses in-session objects; expire so the calendar reads
    # persisted state, as a real request session would.
    session.expire_all()
    calendar = await coach_calendar(
        session, "c-svc-fk4", from_date=date(2026, 10, 1), to_date=date(2026, 12, 31)
    )
    listed = {e.slot_id for e in calendar.entries}
    assert kept_id in listed  # the kept 9:00 slot renders
    assert not (retired_ids & listed)  # retired ones never do
    statuses = {e.status for e in calendar.entries}
    assert statuses <= {"available", "booked", "unavailable"}


@pytest.mark.anyio
async def test_update_cancels_orphaned_booked_slot_and_keeps_matching_one(
    session: AsyncSession,
) -> None:
    """A pattern update cancels booked slots it orphans and keeps ones that still fit."""
    actor = await _coach(session, "c-svc2")
    pattern = await create_pattern(session, actor, _body())

    slots = (
        (
            await session.execute(
                select(Slot).where(Slot.pattern_id == pattern.id).order_by(Slot.start_utc)
            )
        )
        .scalars()
        .all()
    )
    kept_slot, dropped_slot = slots[0], slots[2]  # 9:00 kept; 9:40 dropped by 9:00–9:40 window
    for i, slot in enumerate((kept_slot, dropped_slot)):
        slot.status = "booked"
        session.add(
            Appointment(
                id=f"appt-svc2-{i}",
                slot_id=slot.id,
                coach_id="c-svc2",
                member_id="m-1",
                programme_id="p-1",
                created_by="member",
                acting_user_id="m-1",
                idempotency_key=f"m-1:{slot.id}",
            )
        )
    await session.commit()

    await update_pattern(session, actor, pattern, _body(end_time_local=time(9, 40)))

    kept = await session.get(Appointment, "appt-svc2-0")
    dropped = await session.get(Appointment, "appt-svc2-1")
    assert kept is not None and kept.status == "confirmed"
    assert dropped is not None and dropped.status == "cancelled"
    assert dropped.cancelled_by == "coach"
    # The cancelled booking releases its member:slot key; the kept one retains it.
    assert dropped.idempotency_key is None
    assert kept.idempotency_key == f"m-1:{kept_slot.id}"
    assert pattern.version == 2

    cancelled_events = (
        (
            await session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == "AppointmentCancelled")
            )
        )
        .scalars()
        .all()
    )
    assert any(e.payload.get("appointment_id") == "appt-svc2-1" for e in cancelled_events)
    notes = (
        (
            await session.execute(
                select(Notification).where(
                    Notification.notification_type == "appointment_alternatives_suggested",
                    Notification.recipient_id == "m-1",
                )
            )
        )
        .scalars()
        .all()
    )
    assert any(n.payload.get("appointment_id") == "appt-svc2-1" for n in notes)
    assert all("booking_page_link" in n.payload for n in notes)


@pytest.mark.anyio
async def test_delete_removes_future_unbooked_slots(session: AsyncSession) -> None:
    """Deleting a pattern removes its future unbooked slots."""
    actor = await _coach(session, "c-svc3")
    pattern = await create_pattern(session, actor, _body())
    await delete_pattern(session, actor, pattern)

    remaining = (
        await session.execute(
            select(func.count())
            .select_from(Slot)
            .where(Slot.pattern_id == pattern.id, Slot.status != "cancelled")
        )
    ).scalar_one()
    assert remaining == 0
    assert pattern.status == "deleted"


@pytest.mark.anyio
async def test_impact_dry_run_lists_dropped_booked_appointments_only(
    session: AsyncSession,
) -> None:
    """The impact dry run lists only the booked appointments an update would drop."""
    actor = await _coach(session, "c-svc4")
    pattern = await create_pattern(session, actor, _body())
    slots = (
        (
            await session.execute(
                select(Slot).where(Slot.pattern_id == pattern.id).order_by(Slot.start_utc)
            )
        )
        .scalars()
        .all()
    )
    slots[2].status = "booked"
    session.add(
        Appointment(
            id="appt-svc4-0",
            slot_id=slots[2].id,
            coach_id="c-svc4",
            member_id="m-3",
            programme_id="p-1",
            created_by="member",
            acting_user_id="m-3",
        )
    )
    await session.commit()

    narrower = await pattern_impact(session, pattern, _body(end_time_local=time(9, 40)))
    assert [i.appointment_id for i in narrower] == ["appt-svc4-0"]
    assert narrower[0].member_name == "Sam Nguyen"  # kernel name on the preview

    unchanged = await pattern_impact(session, pattern, _body())
    assert unchanged == []

    deletion = await pattern_impact(session, pattern, None)
    assert [i.appointment_id for i in deletion] == ["appt-svc4-0"]

    # Dry-run must not have changed anything.
    appt = await session.get(Appointment, "appt-svc4-0")
    assert appt is not None and appt.status == "confirmed"
    assert pattern.version == 1


@pytest.mark.anyio
async def test_update_regenerates_to_horizon(session: AsyncSession) -> None:
    """A pattern update regenerates slots out to the rolling horizon."""
    actor = await _coach(session, "c-svc5")
    pattern = await create_pattern(session, actor, _body())
    before = (
        await session.execute(
            select(func.count()).select_from(Slot).where(Slot.pattern_id == pattern.id)
        )
    ).scalar_one()

    await update_pattern(session, actor, pattern, _body(days_of_week=[0, 1]))  # add Tuesdays

    after = (
        await session.execute(
            select(func.count())
            .select_from(Slot)
            .where(Slot.pattern_id == pattern.id, Slot.status == "available")
        )
    ).scalar_one()
    assert after > before
    assert pattern.generation_watermark is not None
    assert pattern.generation_watermark >= NOW.date() + timedelta(weeks=3)
