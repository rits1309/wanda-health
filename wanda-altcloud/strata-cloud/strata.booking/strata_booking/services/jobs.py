"""Background jobs: idempotent, run on demand — no external scheduler in Phase 1.

- ``generate_slots``: rolling top-up — materialises every active availability pattern up to its
  coach's horizon as the horizon rolls forward. Safe to re-run (unique slot constraint +
  upsert-skip).
- ``send_due_reminders``: sends each pending reminder that has come due — a
  notification row per recipient plus a ``ReminderSent`` outbox event, and the reminder marked
  ``sent`` in the same transaction, so retries never double-send. Reminders whose appointment is
  no longer confirmed are defensively cancelled (cancel/reschedule already cancels them; this
  covers any missed by future flows).

- ``detect_no_shows`` (:mod:`strata_booking.services.no_show`): confirmed appointments past
  ``start + grace`` with no call record become ``no_show``; a call found marks ``completed``.

Execution: ``inv jobs-run`` locally, ``POST /v1/dev/jobs/run`` for tests/demos, and an optional
in-process loop (``STRATA_JOBS_INTERVAL_SECONDS``, default off).
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.booking import Appointment, AvailabilityPattern, Reminder, Slot

from strata_booking.core.clock import clock
from strata_booking.services.no_show import detect_no_shows
from strata_booking.services.notifications import get_sender
from strata_booking.services.outbox import emit
from strata_booking.services.slot_generation import generate_slots_for_pattern, horizon_end

__all__ = ["detect_no_shows", "generate_slots", "run_all", "send_due_reminders"]

_log = structlog.get_logger("jobs")


async def generate_slots(session: AsyncSession) -> dict[str, int]:
    """Top up every active pattern to its rolling horizon; commits. Returns counts."""
    patterns = (
        (
            await session.execute(
                select(AvailabilityPattern).where(AvailabilityPattern.status == "active")
            )
        )
        .scalars()
        .all()
    )
    today = clock.now().date()
    created = 0
    for pattern in patterns:
        cap = min(pattern.active_to, await horizon_end(session, pattern.coach_id))
        if pattern.generation_watermark is None or pattern.generation_watermark < cap:
            start = (
                today
                if pattern.generation_watermark is None
                else max(today, pattern.generation_watermark)
            )
            created += await generate_slots_for_pattern(
                session, pattern, from_date=start, to_date=cap
            )
    await session.commit()
    return {"slots_created": created}


async def send_due_reminders(session: AsyncSession) -> dict[str, int]:
    """Send every pending reminder past due; commits. Returns counts."""
    now = clock.now()
    due = (
        (
            await session.execute(
                select(Reminder)
                .where(Reminder.status == "pending", Reminder.due_at_utc <= now)
                .order_by(Reminder.due_at_utc)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    sender = get_sender()
    sent = cancelled = 0
    for reminder in due:
        appointment = await session.get(Appointment, reminder.appointment_id)
        if appointment is None or appointment.status != "confirmed":
            reminder.status = "cancelled"
            cancelled += 1
            continue
        slot = await session.get(Slot, appointment.slot_id)
        await sender.send(
            session,
            channel="email",
            recipient_kind="coach" if reminder.recipient_kind == "coach" else "member",
            recipient_id=reminder.recipient_id,
            notification_type="appointment_reminder",
            payload={
                "appointment_id": appointment.id,
                "coach_id": appointment.coach_id,
                "member_id": appointment.member_id,
                "start_utc": slot.start_utc.isoformat() if slot else None,
            },
        )
        reminder.status = "sent"
        reminder.sent_at = now
        emit(
            session,
            "ReminderSent",
            {
                "reminder_id": reminder.id,
                "appointment_id": appointment.id,
                "recipient_kind": reminder.recipient_kind,
                "recipient_id": reminder.recipient_id,
                "due_at_utc": reminder.due_at_utc.isoformat(),
            },
        )
        sent += 1
    await session.commit()
    return {"reminders_sent": sent, "reminders_cancelled": cancelled}


async def run_all(session: AsyncSession) -> dict[str, int]:
    """Run every due job once; returns the merged counts."""
    results = {
        **await generate_slots(session),
        **await send_due_reminders(session),
        **await detect_no_shows(session),
    }
    _log.info("jobs_run", **results)
    return results
