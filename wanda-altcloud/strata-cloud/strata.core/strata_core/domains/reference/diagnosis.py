"""ORM model for ICD-10-CM diagnosis reference codes.

One row per valid (billable) ICD-10-CM code from the CMS order file: the code (stored without
a decimal, exactly as the source and the legacy UI present it — e.g. ``G932`` for G93.2) plus
its abbreviated (short) and long titles. Substring search over the long title is backed by a
pg_trgm GIN index; ``code`` is unique (btree) for exact/prefix lookups.

Ported shape-for-shape from ``strata.engine`` at — including its
BigInteger identity keys, which predate the string-UUID convention and are kept
because the port must be behaviour-preserving (SC-2): the ingest pipelines rely
on server-generated identity keys, and these ids never cross the /v1 wire — the
natural keys (NDC / ICD-10 codes) do. Engine deleted its copy at.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Identity, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from strata_core.db.base import Base


class Diagnosis(Base):
    __tablename__ = "diagnoses"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True)  # ICD-10-CM code, no decimal
    short_title: Mapped[str] = mapped_column(Text)
    long_title: Mapped[str] = mapped_column(Text)
    order_number: Mapped[int] = mapped_column(Integer)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        Index(
            "ix_diagnoses_long_title_trgm",
            "long_title",
            postgresql_using="gin",
            postgresql_ops={"long_title": "gin_trgm_ops"},
        ),
    )
