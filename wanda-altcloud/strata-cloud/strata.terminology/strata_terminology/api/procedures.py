"""Procedure-lookup routes — ICD-10-PCS search (the web service behind Summit's picker)."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from strata_terminology.core.config import settings
from strata_terminology.db.session import get_session
from strata_terminology.schemas.procedure import ProcedureSearchResponse
from strata_terminology.services.procedures import search_procedures

router = APIRouter(prefix="/procedures", tags=["procedures"])


@router.get("/search", response_model=ProcedureSearchResponse, summary="Search procedures")
async def search(
    q: str = Query(min_length=1, description="ICD-10-PCS code prefix or description substring."),
    session: AsyncSession = Depends(get_session),
) -> ProcedureSearchResponse:
    results = await search_procedures(session, q, limit=settings.procedure_search_limit)
    return ProcedureSearchResponse(query=q, count=len(results), results=results)
