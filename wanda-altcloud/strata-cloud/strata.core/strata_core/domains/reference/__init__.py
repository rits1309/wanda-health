"""Reference data: medications (FDA NDC), diagnoses (ICD-10-CM), procedures (ICD-10-PCS).

Ported shape-for-shape from ``strata.engine`` at; engine deletes
its copies and consumes these. Writer: ``strata.engine`` (ingest
pipelines) — see ``strata_core.ownership``.
"""

from strata_core.domains.reference.diagnosis import Diagnosis
from strata_core.domains.reference.medication import (
    ActiveIngredient,
    Packaging,
    Product,
    ProductIdentifier,
)
from strata_core.domains.reference.procedure import Procedure

__all__ = [
    "ActiveIngredient",
    "Diagnosis",
    "Packaging",
    "Procedure",
    "Product",
    "ProductIdentifier",
]
