"""The wire types carry profile identity by profile id — never the Cognito subject,
and build straight from ORM objects (the from_attributes contract relies on).

Since Identity ``cognito_sub`` is gone from the contract:
clients key on ``id``; the subject is internal to resolution
(``kernel.identifiers``). Since the ``languages`` wire field is derived
from the kernel's ``user_languages`` link rows (``UserProfile.language_codes``);
the wire shape itself is unchanged.
"""

from strata_core.domains.kernel import UserLanguage, UserProfile

from strata_identity.schemas import UserProfileOut


def test_user_profile_out_builds_from_the_model() -> None:
    """UserProfileOut validates straight from the ORM model, languages from the links."""
    profile = UserProfile(
        id="u-1",
        email="user@wandahealth.com",
        first_name="Test",
        last_name="User",
        display_name="Test User",
        timezone="Europe/London",
    )
    profile.user_languages = [
        UserLanguage(user_id="u-1", language_code="en"),
        UserLanguage(user_id="u-1", language_code="es"),
    ]
    out = UserProfileOut.model_validate(profile)
    assert out.id == "u-1"
    assert out.languages == ["en", "es"]  # derived from the link rows, wire name unchanged
    assert out.roles == []  # roles are supplied by the caller (token claims), defaulting empty


def test_the_contract_carries_no_cognito_sub() -> None:
    """UserProfileOut carries no cognito_sub field - it left the contract at."""
    assert "cognito_sub" not in UserProfileOut.model_fields  #


def test_the_wire_field_still_populates_by_its_own_name() -> None:
    """Callers and tests that construct the wire type directly keep working."""
    out = UserProfileOut(
        id="u-1",
        email="user@wandahealth.com",
        first_name="Test",
        last_name="User",
        display_name="Test User",
        timezone="Europe/London",
        languages=["en"],
    )
    assert out.languages == ["en"]
