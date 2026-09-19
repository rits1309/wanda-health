"""Pydantic response models for the diagnosis-lookup API."""

from pydantic import BaseModel, Field


class Diagnosis(BaseModel):
    code: str = Field(description="ICD-10-CM diagnosis code (no decimal), e.g. 'G932'.")
    long_title: str = Field(description="Full diagnosis description.")
    short_title: str = Field(description="Abbreviated diagnosis description.")


class DiagnosisSearchResponse(BaseModel):
    query: str = Field(description="The search term that was applied.")
    count: int = Field(description="Number of results returned.")
    results: list[Diagnosis]
