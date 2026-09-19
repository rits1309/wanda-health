"""Medication-lookup routes — the web service behind Summit's enrollment lookup."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from strata_terminology.core.config import settings
from strata_terminology.db.session import get_session
from strata_terminology.schemas.medication import MedicationSearchResponse
from strata_terminology.services.medications import search_medications

router = APIRouter(prefix="/medications", tags=["medications"])


@router.get("/search", response_model=MedicationSearchResponse, summary="Search medications")
async def search(
    q: str = Query(min_length=1, description="Brand or generic name substring."),
    session: AsyncSession = Depends(get_session),
) -> MedicationSearchResponse:
    results = await search_medications(session, q, limit=settings.medication_search_limit)
    return MedicationSearchResponse(query=q, count=len(results), results=results)
