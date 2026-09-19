"""The clinical domain: a member's health profile (design plan Part B).

Diagnoses, procedures, and medications link members to Strata.Terminology's
reference catalogues by **natural key** (ICD-10 code / FDA product_id) with no
cross-domain DDL FK — the reference datasets are refresh-regenerated, so their
surrogate ids never anchor anything; validation is service-level.
Demographics complete the profile one-to-one.

Period semantics: the link tables carry ``starts_on``/``ends_on`` and
upcoming/current/historic are derived from the dates — no status flag, no
uniqueness constraints (recurrence = multiple rows); "no overlapping open
periods for the same code" is a service-level rule, never DDL.

Every column is classified ``clinical`` ( rule 6; the catalog in
``strata_core.classifications``). Writer (see ``strata_core.ownership``): the
future member-profile service — the domain lands ahead of it deliberately
( option-5 separability); until it exists nothing writes here but
fixtures and the imports.
"""

from strata_core.domains.clinical.demographics import MemberDemographics
from strata_core.domains.clinical.diagnosis import MemberDiagnosis
from strata_core.domains.clinical.medication import MemberMedication
from strata_core.domains.clinical.procedure import MemberProcedure

__all__ = [
    "MemberDemographics",
    "MemberDiagnosis",
    "MemberMedication",
    "MemberProcedure",
]
