"""Platform tables: the transactional outbox, the local notification store, call records.

- ``outbox_events``: every significant business event, written in the same transaction as the
  state change (the preferred baseline). ``published_at`` stays NULL in Phase 1 —
  the relay to Elevate bronze is a later phase.
- ``notifications``: the Phase 1 ``LocalStoreSender`` writes here instead of SES/SNS; viewable
  via the dev endpoint so suggested-alternatives emails and reminders can be demonstrated.
- ``call_records``: the stub Twilio source for no-show detection, populated via the
  dev endpoint.

Ported shape-for-shape from ``strata.booking``, except that the
people columns (``coach_id``/``member_id``) now reference the kernel's
``user_profiles`` — the local coach/member/admin stand-ins are gone;
role-ness is enforced by service-level guards, never DDL.
"""

from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from strata_core.db.base import Base
from strata_core.db.common import TimestampMixin, new_id


class OutboxEvent(TimestampMixin, Base):
    __tablename__ = "outbox_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Notification(TimestampMixin, Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    channel: Mapped[str] = mapped_column(String, nullable=False)  # email | push
    recipient_kind: Mapped[str] = mapped_column(String, nullable=False)  # member | coach
    recipient_id: Mapped[str] = mapped_column(String, nullable=False)
    notification_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class CallRecord(TimestampMixin, Base):
    __tablename__ = "call_records"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=new_id)
    coach_id: Mapped[str] = mapped_column(String, nullable=False)
    member_id: Mapped[str] = mapped_column(String, nullable=False)
    initiated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
