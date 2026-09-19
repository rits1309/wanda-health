"""Diagnosis search — the seam.

Owns search over the ICD-10-CM diagnosis store. Callers (the API route, future consumers)
depend only on ``search_diagnoses``, so the store can change without touching them. Substring
matching over the long title is backed by the pg_trgm GIN index; exact/prefix code matching
uses the unique ``code`` index.
"""

from __future__ import annotations

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.reference import Diagnosis as DiagnosisRow

from strata_terminology.schemas.diagnosis import Diagnosis


def _like_escape(term: str) -> str:
    """Escape LIKE/ILIKE wildcards so a user's query matches literally (substring search)."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def search_diagnoses(session: AsyncSession, query: str, *, limit: int) -> list[Diagnosis]:
    """Case-insensitive search over ICD-10-CM code and long title.

    Matches a code prefix or a substring of the long title. Ranked: exact code, then code
    prefix, then title prefix, then mid-title match; ties break by code. A user-typed decimal
    is stripped (codes are stored without one, so ``G93.2`` still matches ``G932``). A blank
    query returns nothing.
    """
    term = query.replace("\x00", "").replace(".", "").strip().lower()
    if not term:
        return []

    escaped = _like_escape(term)
    contains = f"%{escaped}%"
    prefix = f"{escaped}%"

    code_l = func.lower(DiagnosisRow.code)
    title_l = func.lower(DiagnosisRow.long_title)
    rank = case(
        (code_l == term, 0),
        (code_l.like(prefix, escape="\\"), 1),
        (title_l.like(prefix, escape="\\"), 2),
        else_=3,
    )

    stmt = (
        select(DiagnosisRow.code, DiagnosisRow.long_title, DiagnosisRow.short_title)
        .where(
            or_(
                DiagnosisRow.code.ilike(prefix, escape="\\"),
                DiagnosisRow.long_title.ilike(contains, escape="\\"),
            )
        )
        .order_by(rank, DiagnosisRow.code)
        .limit(limit)
    )

    rows = (await session.execute(stmt)).all()
    return [
        Diagnosis(code=r.code, long_title=r.long_title, short_title=r.short_title) for r in rows
    ]
