"""Development seed — DEV ONLY, never the real product.

Provisions the deterministic dev cast into the savanna-dev pool AND the
database, exercising the same dual-write rule the future admin flows will use:
`user_roles` is authoritative and Cognito group membership mirrors it — so the
script finishes with a **reconciliation assertion** (groups == user_roles for
every seeded user) and fails loudly on drift.

Since the cast is defined ONCE, in ``strata_core.fixtures.cast``, and the
**database half comes from the canonical `demo` fixture profile** (profiles,
role assignments, member details — loaded first). This script keeps the Cognito
half (pool users, passwords, group membership) and then adopts each real pool
subject onto the canonical profile (the profile rows carry deterministic
placeholder subs until then; the cast's identity key is the email).

Pool users are created **by cast username** with the email as a verified
attribute — the pool signs in by username with email as an ALIAS,
and an alias pool rejects email-format usernames.

Idempotent: safe to re-run; existing users/rows are converged, not duplicated.
Guarded: refuses to run unless STRATA_DEV_MODE=true AND the environment is local.
Cognito users are created with admin_create_user(MessageAction=SUPPRESS) +
admin_set_user_password(Permanent) so seeding never sends real emails.

Run:  inv seed   (needs developer AWS credentials — `aws login`;
                  STRATA_AWS_PROFILE selects a named profile)
"""

import asyncio
import sys
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from strata_core.domains.kernel import COGNITO_SUB, UserProfile
from strata_core.fixtures import load_profile
from strata_core.fixtures.cast import DEMO_CAST, CastMember
from strata_identity.identifiers import adopt
from strata_identity.profiles import get_profile
from strata_identity.roles import GROUP_BY_ROLE, ROLE_BY_GROUP, roles_for

from strata_engine_auth.core.config import settings
from strata_engine_auth.services.cognito import admin_cognito

# Fixed, documented, dev-pool-only.
PASSWORD = "Savanna-Dev!2026"  # noqa: S105


def reconciliation_mismatches(
    groups_by_email: dict[str, set[str]], roles_by_email: dict[str, set[str]]
) -> list[str]:
    """Compare Cognito group membership against user_roles (both as catalogue names).

    Pure — unit-tested. Unknown groups (not in the role vocabulary) are ignored,
    matching the verifier's behaviour.
    """
    problems: list[str] = []
    for email in sorted(set(groups_by_email) | set(roles_by_email)):
        from_groups = {
            ROLE_BY_GROUP[g] for g in groups_by_email.get(email, set()) if g in ROLE_BY_GROUP
        }
        from_db = roles_by_email.get(email, set())
        if from_groups != from_db:
            problems.append(
                f"{email}: cognito groups → {sorted(from_groups)}"
                f" but user_roles → {sorted(from_db)}"
            )
    return problems


def _guard() -> None:
    if not settings.dev_mode or settings.environment != "local":
        raise SystemExit(
            "Seeding is a development-only mechanism (never the real product): it requires "
            f"STRATA_DEV_MODE=true and a local environment "
            f"(dev_mode={settings.dev_mode}, STRATA_ENVIRONMENT={settings.environment!r})."
        )


def _ensure_cognito_user(idp: Any, user: CastMember) -> str:
    """Create (or converge) the pool user without sending any email; return the sub.

    The Username is the CAST USERNAME — the sign-in identity on the pool
    (an email-alias pool rejects email-format usernames). The email rides as a
    verified attribute, which is what makes the alias resolve it at login.
    """
    try:
        response = idp.admin_create_user(
            UserPoolId=settings.cognito_user_pool_id,
            Username=user.username,
            MessageAction="SUPPRESS",
            UserAttributes=[
                {"Name": "email", "Value": user.email},
                {"Name": "email_verified", "Value": "true"},
            ],
        )
        attributes = {a["Name"]: a["Value"] for a in response["User"]["Attributes"]}
    except idp.exceptions.UsernameExistsException:
        response = idp.admin_get_user(
            UserPoolId=settings.cognito_user_pool_id, Username=user.username
        )
        attributes = {a["Name"]: a["Value"] for a in response["UserAttributes"]}
    # Idempotent and permanent: sets (or resets) the known dev password + CONFIRMED
    # status — also the pool's admin-driven recovery mechanism.
    idp.admin_set_user_password(
        UserPoolId=settings.cognito_user_pool_id,
        Username=user.username,
        Password=PASSWORD,
        Permanent=True,
    )
    return str(attributes["sub"])


def _ensure_groups(idp: Any, user: CastMember) -> set[str]:
    """Converge group membership to the cast exactly — add missing, remove stale.

    Only role-vocabulary groups are touched (any other group is left alone,
    matching the verifier's ignore-unknown behaviour). Returns the converged
    group set, so reconciliation needs no second list call.
    """
    listed = idp.admin_list_groups_for_user(
        UserPoolId=settings.cognito_user_pool_id, Username=user.username
    )
    current = {g["GroupName"] for g in listed["Groups"]}
    wanted = {GROUP_BY_ROLE[role] for role in user.roles}
    for group in wanted - current:
        idp.admin_add_user_to_group(
            UserPoolId=settings.cognito_user_pool_id, Username=user.username, GroupName=group
        )
    for group in (current & set(ROLE_BY_GROUP)) - wanted:
        idp.admin_remove_user_from_group(
            UserPoolId=settings.cognito_user_pool_id, Username=user.username, GroupName=group
        )
    return (current - set(ROLE_BY_GROUP)) | wanted


async def _adopt_subject(session: AsyncSession, sub: str, email: str) -> str:
    """Converge the active ``Cognito Sub`` mapping onto the canonical profile.

    Sub first (a profile already mapped to the pool subject wins — never trips
    the active-mapping unique index), then the EARLIEST row for the email:
    emails are deliberately non-unique, and /me lazy-provisioning can add a
    duplicate after a pool recreation. The adoption itself is the identifiers
    seam's ``adopt(exclusive=True)`` — the profile's previous active mapping
    (the fixture placeholder, or a pre-recreation subject) is ENDED, never
    deleted: the auditable re-link.
    """
    profile = await get_profile(session, sub)
    if profile is None:
        profile = (
            (
                await session.execute(
                    select(UserProfile)
                    .where(UserProfile.email == email)
                    .order_by(UserProfile.created_at, UserProfile.id)
                )
            )
            .scalars()
            .first()
        )
        if profile is None:
            raise RuntimeError(
                f"no profile for {email!r} — load the demo fixture profile before adopting"
            )
        await adopt(
            session,
            user_id=profile.id,
            type_name=COGNITO_SUB,
            external_id=sub,
            exclusive=True,
        )
        await session.commit()
    return profile.id


async def seed() -> None:
    _guard()
    # The service's CREDENTIALED client (admin_* operations need signing, unlike
    # the runtime UNSIGNED client) — honours STRATA_AWS_PROFILE (Identity).
    idp = admin_cognito()
    engine = create_async_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    groups_by_email: dict[str, set[str]] = {}
    roles_by_email: dict[str, set[str]] = {}
    try:
        # The database half: the canonical `demo` fixture profile
        # profiles, role assignments, member details, converged exactly.
        await load_profile(engine, "demo")
        for user in DEMO_CAST:
            email = user.email
            if email is None:  # pragma: no cover — the demo cast is fully login-capable
                raise RuntimeError(f"demo cast member {user.username!r} has no email")
            sub = _ensure_cognito_user(idp, user)
            groups_by_email[email] = _ensure_groups(idp, user)
            async with sessionmaker() as session:
                profile_id = await _adopt_subject(session, sub, email)
                roles_by_email[email] = set(await roles_for(session, profile_id))
            roles_note = sorted(roles_by_email[email])
            print(f"  {user.effective_display_name:<12} {email:<32} roles={roles_note}")
    finally:
        await engine.dispose()

    problems = reconciliation_mismatches(groups_by_email, roles_by_email)
    if problems:
        print("\nRECONCILIATION FAILED — Cognito groups and user_roles have drifted:")
        for problem in problems:
            print(f"  {problem}")
        sys.exit(1)
    print(f"\nSeeded {len(DEMO_CAST)} users; reconciliation clean (groups == user_roles).")
    # PASSWORD is the same fixed, publicly-documented, dev-pool-only
    # constant declared above (already noqa'd for bandit's S105) -- this
    # print is how a developer running this dev-only seed script learns
    # what to sign in with, not a real credential leaking into a log.
    # CodeQL's py/clear-text-logging-sensitive-data flags this print by
    # pattern alone; this file is excluded from CodeQL analysis entirely
    # via reusable-secops-scan.yml's paths-ignore rather than suppressed
    # here inline -- an inline lgtm[...]/codeql[...] comment only affects
    # GitHub's own Code Scanning UI after an upload this pipeline never
    # does, so it has no effect on the raw SARIF this pipeline's gate
    # actually reads (confirmed by two real runs).
    print(f"Password for every seeded user: {PASSWORD}")
    print("Sign in with the username (e.g. 'dana') or the email — the alias resolves either.")


if __name__ == "__main__":
    asyncio.run(seed())
