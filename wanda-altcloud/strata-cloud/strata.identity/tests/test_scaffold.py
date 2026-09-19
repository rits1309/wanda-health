"""The slimmed-package gate: identity logic here, identity schema in strata-core.

The absorption's structural invariants: no models/db/alembic of its own, the
role vocabulary is the kernel's single definition, and the seam surface the
consumers rely on is intact.
"""

import importlib.util

from strata_core.domains.kernel import ROLE_NAMES as KERNEL_ROLE_NAMES

import strata_identity.roles as roles
from strata_identity.schemas import UserProfileOut


def test_the_package_owns_no_schema() -> None:
    """Models, Base and the migration chain moved to strata-core."""
    for gone in ("strata_identity.models", "strata_identity.db"):
        assert importlib.util.find_spec(gone) is None, f"{gone} should not exist any more"


def test_role_vocabulary_is_the_kernels() -> None:
    """The identity role vocabulary IS the kernel's, not a copy."""
    assert roles.ROLE_NAMES is KERNEL_ROLE_NAMES
    assert (roles.ROLE_COACH, roles.ROLE_MEMBER, roles.ROLE_ADMIN) == KERNEL_ROLE_NAMES
    assert set(roles.GROUP_BY_ROLE) == set(KERNEL_ROLE_NAMES)


def test_the_wire_type_is_the_sd2_contract() -> None:
    """The enriched-principal shape /v1/auth/me returns — the consumer contract.

    Extended additively at: the raw editable fields the profile
    page needs ride alongside the effective ``display_name``. Nothing was removed.
    """
    assert set(UserProfileOut.model_fields) == {
        "id",  # the identifier clients key on — cognito_sub left the contract at
        "email",
        "first_name",
        "last_name",
        "display_name",  # the effective name: override, else "First Last"
        "display_name_override",  # the raw nullable override
        "timezone",
        "preferred_language",
        "languages",
        "roles",
    }


def test_testing_helpers_expose_the_token_seam_only() -> None:
    """strata_identity.testing exposes exactly the token seam helpers, nothing more."""
    from strata_identity import testing

    assert set(testing.__all__) == {
        "generate_rsa_key",
        "local_jwks_client",
        "mint_cognito_token",
        "stub_jwks_client",
    }
