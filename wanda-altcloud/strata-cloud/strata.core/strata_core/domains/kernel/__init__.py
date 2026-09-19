"""The shared domain kernel: people, roles, languages, programmes, and identifiers.

The person/role model is the shipped ``strata.identity`` design **ported
shape-for-shape** (its design is settled — do not re-open it here): canonical
identity is the user profile, and every external identity (Cognito subject,
legacy Django id, device identifiers) is a typed mapping row under the
``kernel.identifiers`` sub-domain. Languages are normalised: a catalogue +
``user_languages`` link, with
the preferred language a person-level FK on the profile. Programmes and their
assignments come from booking's Phase 1 local models, with the assignment
gaining real foreign keys now that people live in one table.

Writers (see ``strata_core.ownership``): people → ``strata.engine.auth``;
programmes → ``strata.booking``; identifiers → ``strata.engine.auth`` (the
identity write path).
"""

from strata_core.domains.kernel.identifiers import (
    COGNITO_SUB,
    EXTERNAL_ID_TYPE_IDS,
    EXTERNAL_ID_TYPE_NAMES,
    LEGACY_SUMMIT_COACH_ID,
    LEGACY_SUMMIT_DJANGO_USER_ID,
    LEGACY_SUMMIT_PATIENT_ID,
    UserExternalId,
    UserExternalIdType,
)
from strata_core.domains.kernel.language import LANGUAGE_CODES, Language, UserLanguage
from strata_core.domains.kernel.profile import (
    DEFAULT_PROFILE_TIMEZONE,
    PROFILE_TIMEZONES,
    UserProfile,
)
from strata_core.domains.kernel.programme import Programme, ProgrammeAssignment
from strata_core.domains.kernel.role import ROLE_BY_KIND, ROLE_IDS, ROLE_NAMES, Role, UserRole

__all__ = [
    "COGNITO_SUB",
    "DEFAULT_PROFILE_TIMEZONE",
    "EXTERNAL_ID_TYPE_IDS",
    "EXTERNAL_ID_TYPE_NAMES",
    "LANGUAGE_CODES",
    "LEGACY_SUMMIT_COACH_ID",
    "LEGACY_SUMMIT_DJANGO_USER_ID",
    "LEGACY_SUMMIT_PATIENT_ID",
    "PROFILE_TIMEZONES",
    "ROLE_BY_KIND",
    "ROLE_IDS",
    "ROLE_NAMES",
    "Language",
    "Programme",
    "ProgrammeAssignment",
    "Role",
    "UserExternalId",
    "UserExternalIdType",
    "UserLanguage",
    "UserProfile",
    "UserRole",
]
