"""Medication search — the seam.

This module owns the search logic over the drug store. It is the backend counterpart of
Summit's ``src/api/medications.ts#searchMedications``: callers (the API route, the future
Summit frontend) depend only on ``search_medications`` here, so the store can change without
touching them. It now queries PostgreSQL — substring matching is backed by the pg_trgm GIN
indexes on ``brand_name``/``generic_name``; the API contract (``Medication``) is unchanged.
"""

from __future__ import annotations

from sqlalchemy import case, func, literal, or_, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.reference import ActiveIngredient, Product

from strata_terminology.schemas.medication import Medication, MedicationIngredient


def _like_escape(term: str) -> str:
    """Escape LIKE/ILIKE wildcards so a user's query matches literally (substring search)."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def search_medications(session: AsyncSession, query: str, *, limit: int) -> list[Medication]:
    """Case-insensitive substring match over brand and generic name.

    Results are ranked: brand/generic names that *start with* the query come before
    mid-string matches, then alphabetically by brand name. An empty/blank query returns
    nothing (the frontend should not fetch the whole index).
    """
    # Strip NUL bytes — PostgreSQL text cannot contain them, so they'd error a parameterised
    # query rather than simply not matching (which is the intent for a substring search).
    term = query.replace("\x00", "").strip().lower()
    if not term:
        return []

    escaped = _like_escape(term)
    contains = f"%{escaped}%"
    prefix = f"{escaped}%"

    # Brand falls back to generic name (~16% of products have no brand_name), mirroring the
    # frontend's expectation that every result carries a display name.
    brand = func.coalesce(Product.brand_name, Product.generic_name)

    # Strength is presented as a single string; rebuild it from the (ordered) active
    # ingredients, e.g. "2.4 mg/0.75mL" or "5 mg/1; 10 mg/1". string_agg skips NULL strengths.
    strength = (
        select(
            func.string_agg(
                ActiveIngredient.strength,
                aggregate_order_by(literal("; "), ActiveIngredient.ord),
            )
        )
        .where(ActiveIngredient.product_pk == Product.id)
        .correlate(Product)
        .scalar_subquery()
    )

    starts = or_(
        func.lower(Product.brand_name).like(prefix, escape="\\"),
        func.lower(Product.generic_name).like(prefix, escape="\\"),
    )

    stmt = (
        select(
            Product.id,
            Product.product_ndc,
            brand.label("brand_name"),
            Product.generic_name,
            Product.dosage_form,
            strength.label("strength"),
            Product.labeler_name,
            Product.routes,
        )
        .where(
            or_(
                Product.brand_name.ilike(contains, escape="\\"),
                Product.generic_name.ilike(contains, escape="\\"),
            )
        )
        .order_by(case((starts, 0), else_=1), func.lower(brand))
        .limit(limit)
    )

    rows = (await session.execute(stmt)).all()

    # Per-ingredient detail (name + strength, source order) for the returned page only —
    #: Summit's picker renders the ingredient list, not just the joined string.
    ingredients_by_product: dict[int, list[MedicationIngredient]] = {}
    if rows:
        ing_rows = (
            await session.execute(
                select(
                    ActiveIngredient.product_pk, ActiveIngredient.name, ActiveIngredient.strength
                )
                .where(ActiveIngredient.product_pk.in_([r.id for r in rows]))
                .order_by(ActiveIngredient.product_pk, ActiveIngredient.ord)
            )
        ).all()
        for product_pk, name, ing_strength in ing_rows:
            ingredients_by_product.setdefault(product_pk, []).append(
                MedicationIngredient(name=name or "", strength=ing_strength or "")
            )

    return [
        Medication(
            product_ndc=r.product_ndc or "",
            brand_name=r.brand_name or "",
            generic_name=r.generic_name or "",
            dosage_form=r.dosage_form or "",
            strength=r.strength or "",
            labeler_name=r.labeler_name or "",
            routes=list(r.routes or []),
            ingredients=ingredients_by_product.get(r.id, []),
        )
        for r in rows
    ]
