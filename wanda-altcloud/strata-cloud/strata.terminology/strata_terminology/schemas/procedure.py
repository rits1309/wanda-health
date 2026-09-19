"""Pydantic response models for the procedure-lookup API."""

from pydantic import BaseModel, Field


class Procedure(BaseModel):
    code: str = Field(description="ICD-10-PCS procedure code (no decimal), e.g. '02HA0QZ'.")
    long_title: str = Field(description="Full procedure description.")
    short_title: str = Field(description="Abbreviated procedure description.")


class ProcedureSearchResponse(BaseModel):
    query: str = Field(description="The search term that was applied.")
    count: int = Field(description="Number of results returned.")
    results: list[Procedure]
