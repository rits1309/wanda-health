"""One-user legacy import — the "imported" state, DEV ONLY.

Creates what the migration login's gate expects to find: the
canonical profile (+ role assignments; + member detail for a patient) and the
active legacy identifier mappings — ``Legacy Summit Django User ID`` holding
the platform's ``django_user_id``, plus the kind's internal id
(``Legacy Summit Patient ID`` / ``Legacy Summit Coach ID``; legacy data such
as weight readings is keyed on it). Deliberately NOTHING in
Cognito and NO password anywhere: the migration login creates the account
at first sign-in. Passwords never touch this script, the database,
or a repo.

A patient's app username is their login identifier but is NOT stored here —
the legacy platform validates it and returns the ids the gate matches on
(profile data carries the coach email; the gate cross-checks it).
A coach therefore REQUIRES ``--email``.

Idempotent AND convergent: an existing active Django-user-id mapping wins (the
profile is untouched), but a missing internal-id mapping is added on re-run —
the official backfill path for users imported before the split. A
CONFLICTING active internal-id mapping fails loudly; never superseded on a
guess. Guarded like the seed (STRATA_DEV_MODE + local): this phase proves ONE
user end to end; the bulk import is a later phase (owner-directed 20-07-2026)
and will revisit the guard.

Run (from strata.engine.auth; both ids come from the credentials-check call):

    .venv/bin/python -m scripts.import_user \
        --django-id 2253 --internal-id 1560 --kind patient \
        --first-name Theo --last-name VH

    .venv/bin/python -m scripts.import_user \
        --django-id 2513 --internal-id 744 --kind coach \
        --first-name Lewis --last-name "Test Coach" \
        --email lewis+savtest4@wandahealth.com

Every legacy user carries a first and last name — both are
required; ``--display-name`` is the OPTIONAL presentation override.
"""

import argparse
import asyncio
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from strata_core.domains.kernel import (
    LANGUAGE_CODES,
    LEGACY_SUMMIT_COACH_ID,
    LEGACY_SUMMIT_DJANGO_USER_ID,
    LEGACY_SUMMIT_PATIENT_ID,
    UserLanguage,
    UserProfile,
)
from strata_identity.identifiers import active_mappings_for_user, adopt, resolve_profile
from strata_identity.roles import ROLE_COACH, ROLE_MEMBER, assign_role, roles_for

from scripts.seed import _guard
from strata_engine_auth.core.config import settings

ROLE_BY_KIND = {"patient": ROLE_MEMBER, "coach": ROLE_COACH}
INTERNAL_TYPE_BY_KIND = {"patient": LEGACY_SUMMIT_PATIENT_ID, "coach": LEGACY_SUMMIT_COACH_ID}


async def import_user(
    session: AsyncSession,
    *,
    django_id: str,
    internal_id: str,
    kind: Literal["patient", "coach"],
    first_name: str,
    last_name: str,
    display_name: str | None = None,
    email: str | None = None,
    profile_id: str | None = None,
    timezone: str = "Europe/London",
    languages: tuple[str, ...] = ("en",),
    preferred_language: str = "en",
) -> tuple[str, bool]:
    """Converge the imported state for one legacy user; flushes, does not commit.

    Returns ``(profile_id, created)`` — ``created`` False when an active
    Django-user-id mapping already holds this ``django_id`` (idempotent re-run:
    the existing profile wins; a missing internal-id mapping is still added —
    convergence, the backfill path).
    """
    if not first_name.strip() or not last_name.strip():
        raise ValueError(
            "first and last name are required and non-blank — every legacy user "
            "carries both; --display-name is only the optional override"
        )
    if kind == "coach" and email is None:
        raise ValueError(
            "a coach import requires --email: the staff email is their login "
            "identifier, and the migration gate cross-checks it "
        )
    # Fail clean, before any DB write, on language data the normalised kernel would
    # reject (mirrors strata_core create_profile): unknown catalogue codes surface a
    # clear message instead of an opaque FK violation mid-flush, and the preferred
    # language must be one the person actually speaks (the service-level rule).
    unknown = set(languages) - set(LANGUAGE_CODES)
    if unknown:
        raise ValueError(
            f"unknown language codes {sorted(unknown)}; expected among {LANGUAGE_CODES}"
        )
    if preferred_language not in languages:
        raise ValueError(
            f"preferred_language {preferred_language!r} is not among the spoken "
            f"languages {languages} (the service-level rule)"
        )
    internal_type = INTERNAL_TYPE_BY_KIND[kind]
    existing = await resolve_profile(
        session, type_name=LEGACY_SUMMIT_DJANGO_USER_ID, external_id=django_id
    )
    if existing is not None:
        if ROLE_BY_KIND[kind] not in await roles_for(session, existing.id):
            raise ValueError(
                f"--kind {kind!r} contradicts the imported profile's roles; "
                "check the ids — nothing converged"
            )
        await _converge_internal_mapping(session, existing.id, internal_type, internal_id)
        return existing.id, False

    profile = UserProfile(
        email=email,
        first_name=first_name,
        last_name=last_name,
        display_name=display_name,
        timezone=timezone,
        preferred_language_code=preferred_language,
        **({"id": profile_id} if profile_id else {}),
    )
    session.add(profile)
    await session.flush()
    # Spoken languages are the normalised user_languages links; the
    # preferred language is the person-level FK set above.
    for code in languages:
        session.add(UserLanguage(user_id=profile.id, language_code=code))
    await session.flush()
    await assign_role(session, profile.id, ROLE_BY_KIND[kind])
    # Membership is the Member role in user_roles (assigned above); member_details
    # was dropped/, so there is no marker row to write.
    await adopt(
        session, user_id=profile.id, type_name=LEGACY_SUMMIT_DJANGO_USER_ID, external_id=django_id
    )
    await _refuse_foreign_holder(session, profile.id, internal_type, internal_id)
    await adopt(session, user_id=profile.id, type_name=internal_type, external_id=internal_id)
    return profile.id, True


async def _refuse_foreign_holder(
    session: AsyncSession, profile_id: str, type_name: str, external_id: str
) -> None:
    """Refuse a value another profile actively holds: ``adopt`` supersedes by design
    (ends the holder's row), and this script never supersedes on a guess."""
    holder = await resolve_profile(session, type_name=type_name, external_id=external_id)
    if holder is not None and holder.id != profile_id:
        raise ValueError(
            f"another profile actively holds this {type_name!r} value; "
            "resolve by hand (end the wrong row) — this script never supersedes"
        )


async def _converge_internal_mapping(
    session: AsyncSession, profile_id: str, internal_type: str, internal_id: str
) -> None:
    """Add the internal-id mapping a pre-split import lacks; fail loudly on a
    conflicting one — an active mapping is never superseded on a guess."""
    held = await active_mappings_for_user(session, user_id=profile_id, type_name=internal_type)
    values = [mapping.external_id for mapping in held]
    if values == [internal_id]:
        return  # already converged
    if values:
        raise ValueError(
            f"profile already holds a conflicting active {internal_type!r} mapping; "
            "resolve by hand (end the wrong row) — this script never supersedes"
        )
    await _refuse_foreign_holder(session, profile_id, internal_type, internal_id)
    await adopt(session, user_id=profile_id, type_name=internal_type, external_id=internal_id)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--django-id", required=True, help="the platform's django_user_id")
    parser.add_argument(
        "--internal-id",
        required=True,
        help="the platform's internal_patient_id / internal_client_user_id (by --kind)",
    )
    parser.add_argument("--kind", required=True, choices=("patient", "coach"))
    parser.add_argument("--first-name", required=True, help="every legacy user has one ")
    parser.add_argument("--last-name", required=True, help="every legacy user has one ")
    parser.add_argument("--display-name", default=None, help="optional presentation override ")
    parser.add_argument("--email", default=None, help="required for a coach; optional otherwise")
    parser.add_argument("--profile-id", default=None, help="default: a generated UUID")
    parser.add_argument("--timezone", default="Europe/London")
    parser.add_argument("--languages", default="en", help="comma-separated, e.g. en,es")
    parser.add_argument(
        "--preferred-language", default="en", help="person-level; must be among --languages"
    )
    return parser.parse_args()


async def main() -> None:
    _guard()
    args = _parse_args()
    engine = create_async_engine(settings.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            profile_id, created = await import_user(
                session,
                django_id=args.django_id,
                internal_id=args.internal_id,
                kind=args.kind,
                first_name=args.first_name,
                last_name=args.last_name,
                display_name=args.display_name,
                email=args.email,
                profile_id=args.profile_id,
                timezone=args.timezone,
                languages=tuple(args.languages.split(",")),
                preferred_language=args.preferred_language,
            )
            await session.commit()
    finally:
        await engine.dispose()
    state = "imported" if created else "already imported — converged"
    print(
        f"{profile_id}: {state} (Django user id {args.django_id},"
        f" {args.kind} internal id {args.internal_id}, no Cognito mapping)"
    )
    if created:
        print("Their first login now runs the migration and adopts the account.")


if __name__ == "__main__":
    asyncio.run(main())
