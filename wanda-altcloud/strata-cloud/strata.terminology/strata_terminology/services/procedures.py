"""Procedure search — the seam.

Owns search over the ICD-10-PCS procedure store. Callers (the API route, future consumers)
depend only on ``search_procedures``, so the store can change without touching them. Substring
matching over the long title is backed by the pg_trgm GIN index; exact/prefix code matching
uses the unique ``code`` index.
"""

from __future__ import annotations

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.reference import Procedure as ProcedureRow

from strata_terminology.schemas.procedure import Procedure


def _like_escape(term: str) -> str:
    """Escape LIKE/ILIKE wildcards so a user's query matches literally (substring search)."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def search_procedures(session: AsyncSession, query: str, *, limit: int) -> list[Procedure]:
    """Case-insensitive search over ICD-10-PCS code and long title.

    Matches a code prefix or a substring of the long title. Ranked: exact code, then code
    prefix, then title prefix, then mid-title match; ties break by code. A user-typed decimal
    is stripped (codes are stored without one). A blank query returns nothing.
    """
    term = query.replace("\x00", "").replace(".", "").strip().lower()
    if not term:
        return []

    escaped = _like_escape(term)
    contains = f"%{escaped}%"
    prefix = f"{escaped}%"

    code_l = func.lower(ProcedureRow.code)
    title_l = func.lower(ProcedureRow.long_title)
    rank = case(
        (code_l == term, 0),
        (code_l.like(prefix, escape="\\"), 1),
        (title_l.like(prefix, escape="\\"), 2),
        else_=3,
    )

    stmt = (
        select(ProcedureRow.code, ProcedureRow.long_title, ProcedureRow.short_title)
        .where(
            or_(
                ProcedureRow.code.ilike(prefix, escape="\\"),
                ProcedureRow.long_title.ilike(contains, escape="\\"),
            )
        )
        .order_by(rank, ProcedureRow.code)
        .limit(limit)
    )

    rows = (await session.execute(stmt)).all()
    return [
        Procedure(code=r.code, long_title=r.long_title, short_title=r.short_title) for r in rows
    ]
