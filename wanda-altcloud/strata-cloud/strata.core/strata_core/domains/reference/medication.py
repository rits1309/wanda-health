"""ORM models for the FDA NDC medication data (Option D: normalized core + JSONB tail).

The source openFDA NDC record fans out into several collections; we normalize the entities a
feature will query or display (`products`, `active_ingredients`, `packagings`, and the
interoperability identifiers in `product_identifiers`), keep low-cardinality `routes` as a
Postgres array, and keep `pharm_classes` plus the full `raw` record as JSONB so ingest is
lossless and future fields need no migration.

Keys reflect the data: ``product_id`` is the only unique natural key, ``product_ndc`` is not
unique (so it is indexed, not constrained), and most scalars are nullable (e.g. ~16% of
records have no ``brand_name``). Search is backed by pg_trgm GIN indexes on the name columns.

Ported shape-for-shape from ``strata.engine`` at — including its
BigInteger identity keys, which predate the string-UUID convention and are kept
because the port must be behaviour-preserving (SC-2): the ingest pipelines rely
on server-generated identity keys, and these ids never cross the /v1 wire — the
natural keys (NDC / ICD-10 codes) do. Engine deleted its copy at.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from strata_core.db.base import Base


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    product_id: Mapped[str] = mapped_column(Text, unique=True)  # FDA natural key (unique)
    product_ndc: Mapped[str] = mapped_column(Text)  # NOT unique — indexed only

    brand_name: Mapped[str | None] = mapped_column(Text)
    brand_name_base: Mapped[str | None] = mapped_column(Text)
    generic_name: Mapped[str | None] = mapped_column(Text)
    labeler_name: Mapped[str | None] = mapped_column(Text)
    dosage_form: Mapped[str | None] = mapped_column(Text)
    product_type: Mapped[str | None] = mapped_column(Text)
    marketing_category: Mapped[str | None] = mapped_column(Text)
    marketing_start_date: Mapped[date | None] = mapped_column(Date)
    marketing_end_date: Mapped[date | None] = mapped_column(Date)
    listing_expiration_date: Mapped[date | None] = mapped_column(Date)
    dea_schedule: Mapped[str | None] = mapped_column(Text)
    application_number: Mapped[str | None] = mapped_column(Text)
    spl_id: Mapped[str | None] = mapped_column(Text)
    finished: Mapped[bool | None] = mapped_column(Boolean)

    routes: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    pharm_classes: Mapped[list[dict[str, str]] | None] = mapped_column(JSONB)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    active_ingredients: Mapped[list["ActiveIngredient"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", passive_deletes=True
    )
    packagings: Mapped[list["Packaging"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", passive_deletes=True
    )
    identifiers: Mapped[list["ProductIdentifier"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        Index("ix_products_ndc", "product_ndc"),
        Index(
            "ix_products_brand_trgm",
            "brand_name",
            postgresql_using="gin",
            postgresql_ops={"brand_name": "gin_trgm_ops"},
        ),
        Index(
            "ix_products_generic_trgm",
            "generic_name",
            postgresql_using="gin",
            postgresql_ops={"generic_name": "gin_trgm_ops"},
        ),
    )


class ActiveIngredient(Base):
    __tablename__ = "active_ingredients"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    product_pk: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    ord: Mapped[int] = mapped_column(SmallInteger)
    name: Mapped[str] = mapped_column(Text)
    strength: Mapped[str | None] = mapped_column(Text)

    product: Mapped["Product"] = relationship(back_populates="active_ingredients")

    __table_args__ = (
        Index(
            "ix_ai_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )


class Packaging(Base):
    __tablename__ = "packagings"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    product_pk: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    package_ndc: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    marketing_start_date: Mapped[date | None] = mapped_column(Date)
    marketing_end_date: Mapped[date | None] = mapped_column(Date)
    sample: Mapped[bool | None] = mapped_column(Boolean)

    product: Mapped["Product"] = relationship(back_populates="packagings")

    __table_args__ = (Index("ix_pkg_ndc", "package_ndc"),)


class ProductIdentifier(Base):
    """Cross-system identifiers from ``openfda`` — rxcui / unii / spl_set_id / upc / nui."""

    __tablename__ = "product_identifiers"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    product_pk: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    system: Mapped[str] = mapped_column(Text)
    value: Mapped[str] = mapped_column(Text)

    product: Mapped["Product"] = relationship(back_populates="identifiers")

    __table_args__ = (Index("ix_ident_lookup", "system", "value"),)
