"""Shared model plumbing: string-UUID primary keys and audit timestamps.

String UUIDs are the canonical key convention (owner decision
09-07-2026) for the kernel and every new table; ported reference tables keep
the key shape they shipped with (see ``strata_core.domains.reference``).
``created_at``/``updated_at`` are set by the database.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


def new_id() -> str:
    return uuid4().hex


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
