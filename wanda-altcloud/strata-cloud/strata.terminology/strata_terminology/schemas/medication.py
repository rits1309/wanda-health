"""Pydantic response models for the medication-lookup API."""

from pydantic import BaseModel, Field


class MedicationIngredient(BaseModel):
    name: str = Field(description="Active-ingredient name, e.g. 'SEMAGLUTIDE'.")
    strength: str = Field(description="This ingredient's strength, e.g. '2.4 mg/0.75mL'.")


class Medication(BaseModel):
    product_ndc: str = Field(description="FDA National Drug Code (product segment).")
    brand_name: str = Field(description="Marketed brand name, e.g. 'Wegovy'.")
    generic_name: str = Field(description="Active ingredient, e.g. 'semaglutide'.")
    dosage_form: str = Field(description="Form, e.g. 'INJECTION, SOLUTION'.")
    strength: str = Field(description="Active-ingredient strength, e.g. '2.4 mg/0.75mL'.")
    labeler_name: str = Field(description="Manufacturer / labeler.")
    routes: list[str] = Field(
        default_factory=list, description="Administration route(s), e.g. ['SUBCUTANEOUS']."
    )
    ingredients: list[MedicationIngredient] = Field(
        default_factory=list, description="Active ingredients, in source order."
    )


class MedicationSearchResponse(BaseModel):
    query: str = Field(description="The search term that was applied.")
    count: int = Field(description="Number of results returned.")
    results: list[Medication]
