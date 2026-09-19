"""The fixture library: factory helpers and named, composable seed profiles.

Profiles are **deterministic and idempotent**: loading one
converges the database to the profile's dataset — rows are upserted by their
natural keys (profile email, role/programme name), never duplicated, and a
re-run changes nothing. Load against any target: the shared dev database
(``inv seed --profile <name>``) or an ephemeral testcontainers instance.

Profiles never touch Cognito: the pool half of the dev cast (users, group
membership) stays with ``strata.engine.auth``'s ``inv seed``, which delegates
its database rows here (from) so the cast is defined once. Fixture rows
carry deterministic placeholder ``Cognito Sub`` mapping values (``seed-sub-<username>``)
that the auth seed reconciles to real pool subjects — it already converges on
email exactly for this reason.
"""

from strata_core.fixtures.factories import (
    assign_role,
    build_diagnosis,
    build_procedure,
    build_product,
    converge_demographics,
    converge_diagnosis,
    converge_medication,
    converge_procedure,
    create_profile,
    register_device,
)
from strata_core.fixtures.runner import PROFILES, load_profile

__all__ = [
    "PROFILES",
    "assign_role",
    "build_diagnosis",
    "build_procedure",
    "build_product",
    "converge_demographics",
    "converge_diagnosis",
    "converge_medication",
    "converge_procedure",
    "create_profile",
    "load_profile",
    "register_device",
]
