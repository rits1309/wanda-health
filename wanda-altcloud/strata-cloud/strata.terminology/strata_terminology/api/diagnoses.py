"""Diagnosis-lookup routes — ICD-10-CM search (the web service behind Summit's picker)."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from strata_terminology.core.config import settings
from strata_terminology.db.session import get_session
from strata_terminology.schemas.diagnosis import DiagnosisSearchResponse
from strata_terminology.services.diagnoses import search_diagnoses

router = APIRouter(prefix="/diagnoses", tags=["diagnoses"])


@router.get("/search", response_model=DiagnosisSearchResponse, summary="Search diagnoses")
async def search(
    q: str = Query(min_length=1, description="ICD-10-CM code prefix or description substring."),
    session: AsyncSession = Depends(get_session),
) -> DiagnosisSearchResponse:
    results = await search_diagnoses(session, q, limit=settings.diagnosis_search_limit)
    return DiagnosisSearchResponse(query=q, count=len(results), results=results)
