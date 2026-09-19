"""The clinical walkthrough dataset — a small health-profile slice for demo members.

Gives the ``clinical-demo`` profile a deterministic slice across all four
clinical tables so downstream work (the B2/B3 imports, the future
member-profile service, pgAdmin walkthroughs) has real-shaped rows to look at.
The slice deliberately exercises the period semantics: Morgan carries a
**current** diagnosis (``ends_on`` null), Sam a **historic** one (``ends_on``
past) plus a recurrence — two rows, same code, different periods, which is
exactly why the link tables have no uniqueness constraint — and Luis an
**upcoming** procedure (``starts_on`` future).

Codes are real vocabulary entries (ICD-10-CM / ICD-10-PCS / FDA product_id
format) but are NOT validated against the reference tables here — linkage is
by natural key with no cross-domain FK; validation belongs to the
writing service. Dates are fixed instants, never now-relative.

Every value in this dataset is fixture data for the demo cast — nothing here
derives from a real person.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from strata_core.fixtures.factories import (
    converge_demographics,
    converge_diagnosis,
    converge_medication,
    converge_procedure,
)


@dataclass(frozen=True)
class DiagnosisRecord:
    row_id: str  # deterministic row id — the link tables have no natural key
    member: str  # historical cast id — resolved through the people map, never literally
    code: str
    starts_on: date | None = None
    ends_on: date | None = None


@dataclass(frozen=True)
class ProcedureRecord:
    row_id: str
    member: str
    code: str
    starts_on: date | None = None
    ends_on: date | None = None
    status: str | None = None


@dataclass(frozen=True)
class MedicationRecord:
    row_id: str
    member: str
    product_id: str
    note: str | None = None
    starts_on: date | None = None
    ends_on: date | None = None


@dataclass(frozen=True)
class DemographicsRecord:
    member: str
    sex: str | None = None
    date_of_birth: date | None = None
    ethnicity: str | None = None
    marital_status: str | None = None
    height_in: Decimal | None = None


DIAGNOSES: tuple[DiagnosisRecord, ...] = (
    # Morgan — current: essential hypertension, ongoing (ends_on null).
    DiagnosisRecord("clin-dx-m1-htn", "m-1", "I10", starts_on=date(2024, 3, 12)),
    # Sam — historic + recurrence: the same migraine code twice with different
    # periods ('s recurrence-is-multiple-rows, no uniqueness by design).
    DiagnosisRecord("clin-dx-m2-migraine-1", "m-2", "G43909", date(2022, 1, 10), date(2022, 9, 30)),
    DiagnosisRecord("clin-dx-m2-migraine-2", "m-2", "G43909", starts_on=date(2025, 11, 2)),
    # Priya — unknown start (null starts_on = treated as current).
    DiagnosisRecord("clin-dx-m5-t2dm", "m-5", "E119"),
)

PROCEDURES: tuple[ProcedureRecord, ...] = (
    # Sam — completed same-day procedure (ends_on = starts_on).
    ProcedureRecord(
        "clin-px-m2-excision", "m-2", "0H5RXZZ", date(2023, 5, 4), date(2023, 5, 4), "completed"
    ),
    # Luis — upcoming (starts_on in the future relative to the fixture era).
    ProcedureRecord("clin-px-m4-scheduled", "m-4", "0DB64Z3", starts_on=date(2027, 2, 15)),
)

MEDICATIONS: tuple[MedicationRecord, ...] = (
    # Morgan — active prescription against the hypertension diagnosis
    # (ends_on null = active); FDA product_id natural-key format.
    MedicationRecord(
        "clin-rx-m1-lisinopril",
        "m-1",
        "0143-1240_a7d33628-8d4e-4de1-9a67-72830a125a62",
        note="10mg once daily",
        starts_on=date(2024, 3, 12),
    ),
    # Sam — historic course, period closed.
    MedicationRecord(
        "clin-rx-m2-sumatriptan",
        "m-2",
        "0069-4200_9e5c3f8b-1d27-4c04-b1a8-63f92f0d5c11",
        note="as needed at onset",
        starts_on=date(2022, 1, 24),
        ends_on=date(2022, 9, 30),
    ),
)

DEMOGRAPHICS: tuple[DemographicsRecord, ...] = (
    # Values are permissive strings on purpose — the legacy vocabularies bind at
    # the B2/B3 import; height in inches.
    DemographicsRecord(
        "m-1",
        sex="F",
        date_of_birth=date(1988, 7, 22),
        ethnicity="white",
        marital_status="U",
        height_in=Decimal("65.0"),
    ),
    DemographicsRecord(
        "m-2",
        sex="M",
        date_of_birth=date(1979, 2, 3),
        ethnicity="white",
        marital_status="M",
        height_in=Decimal("70.5"),
    ),
)


async def load_clinical_data(session: AsyncSession, *, people: dict[str, str]) -> None:
    """Converge the demo clinical slice; ``people`` maps cast ids to actual
    profile ids (reference people through the map, never literally)."""
    for dx in DIAGNOSES:
        await converge_diagnosis(
            session,
            row_id=dx.row_id,
            member_user_id=people[dx.member],
            code=dx.code,
            starts_on=dx.starts_on,
            ends_on=dx.ends_on,
        )
    for px in PROCEDURES:
        await converge_procedure(
            session,
            row_id=px.row_id,
            member_user_id=people[px.member],
            code=px.code,
            starts_on=px.starts_on,
            ends_on=px.ends_on,
            status=px.status,
        )
    for rx in MEDICATIONS:
        await converge_medication(
            session,
            row_id=rx.row_id,
            member_user_id=people[rx.member],
            product_id=rx.product_id,
            note=rx.note,
            starts_on=rx.starts_on,
            ends_on=rx.ends_on,
        )
    for demo in DEMOGRAPHICS:
        await converge_demographics(
            session,
            member_user_id=people[demo.member],
            sex=demo.sex,
            date_of_birth=demo.date_of_birth,
            ethnicity=demo.ethnicity,
            marital_status=demo.marital_status,
            height_in=demo.height_in,
        )
