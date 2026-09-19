"""Provision the checked-in USERS list's database half — develop/uat only.

Cognito account creation is Terraform's job (``modules/auth/cognito``'s
``aws_cognito_user`` resource, driven by ``var.cognito_initial_users`` in
wanda-altcloud-infra-code) — this script never creates or touches a Cognito
user. Its one job is the half Terraform cannot own: adopting each already-
existing Cognito subject onto a real database profile (``user_profiles`` +
the ``Cognito Sub`` mapping in ``user_external_ids``) and assigning a role,
using the exact same ``adopt``/``assign_role`` primitives ``scripts/seed.py``
uses for the local dev cast. A user missing from the pool (Terraform hasn't
applied their entry yet, or the email is wrong) is a loud, named failure —
never a silent skip.

Unlike ``seed.py``/``import_user.py``, this is NOT dev-only: develop and uat
are real, if lower-stakes, environments this script is meant to run against
(by hand via ECS Exec, or as a one-off pipeline step — see
.github/workflows/reusable-provision-users.yml in wanda-altcloud). It
refuses outright to run against prod — provisioning real production
identities from a list committed to source is a materially different, much
higher-stakes decision than a develop/uat test account, and deserves its own
explicit design rather than inheriting this script's behaviour by accident.

To add a user: add a row to USERS below AND a matching entry in
wanda-altcloud-infra-code's cognito_initial_users for the target environment
(same email) — this script only adopts an identity Terraform already
created; it does not invent one.

Run (from strata.engine.auth, inside the deployed container — it needs the
real STRATA_DATABASE_URL/STRATA_COGNITO_USER_POOL_ID, not a local .env):

    python3 -m scripts.provision_users
"""

import asyncio
import sys
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from strata_core.domains.kernel import COGNITO_SUB, UserProfile
from strata_identity.identifiers import adopt
from strata_identity.roles import GROUP_BY_ROLE, ROLE_ADMIN, ROLE_COACH, ROLE_MEMBER, assign_role

from strata_engine_auth.core.config import settings
from strata_engine_auth.services.cognito import admin_cognito

ROLE_NAMES = {ROLE_COACH, ROLE_MEMBER, ROLE_ADMIN}


@dataclass(frozen=True)
class ProvisionedUser:
    email: str
    first_name: str
    last_name: str
    role: str
    timezone: str = "America/New_York"


# The one thing to edit to add/remove a develop or uat test user. The email
# must match a key Terraform has already created via cognito_initial_users
# in the target environment's account -- this script adopts an existing
# Cognito subject, it never creates one.
USERS: tuple[ProvisionedUser, ...] = (
    ProvisionedUser("testuser@wandahealth.com", "Test", "User", ROLE_COACH),
    ProvisionedUser("ritushree.dutta@altcloudai.com", "Ritushree", "Dutta", ROLE_COACH),
)


def _guard() -> None:
    if settings.environment == "prod":
        raise SystemExit(
            "Refusing to run against prod. This script provisions real user "
            "identities from a list committed to source -- fine for develop/uat "
            "test accounts, a materially different decision for production. If "
            "prod support is genuinely wanted, it needs its own explicit design "
            "(e.g. an approval-gated workflow), not silent inheritance from this "
            "guard being loosened."
        )


def _cognito_sub(idp: object, email: str) -> str:
    """The pool subject for an email Terraform already created as a user.

    Raises loudly (boto3's own exception) if the user doesn't exist yet --
    never silently skipped, since a typo'd email or a not-yet-applied
    Terraform entry should fail the run, not quietly provision nothing.
    """
    response = idp.admin_get_user(UserPoolId=settings.cognito_user_pool_id, Username=email)
    attributes = {a["Name"]: a["Value"] for a in response["UserAttributes"]}
    return str(attributes["sub"])


async def _provision_one(session: AsyncSession, idp: object, user: ProvisionedUser) -> str:
    if user.role not in ROLE_NAMES:
        raise ValueError(f"{user.email}: unknown role {user.role!r}; expected one of {ROLE_NAMES}")

    sub = _cognito_sub(idp, user.email)

    profile = (
        (
            await session.execute(
                select(UserProfile)
                .where(UserProfile.email == user.email)
                .order_by(UserProfile.created_at, UserProfile.id)
            )
        )
        .scalars()
        .first()
    )
    if profile is None:
        profile = UserProfile(
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            timezone=user.timezone,
        )
        session.add(profile)
        await session.flush()

    await adopt(session, user_id=profile.id, type_name=COGNITO_SUB, external_id=sub, exclusive=True)
    granted = await assign_role(session, profile.id, user.role)

    # user_roles is authoritative; Cognito group membership must mirror it
    # (the same dual-write rule scripts/seed.py's dev cast follows) --
    # /v1/auth/me resolves the caller's roles from Cognito group
    # membership, NOT user_roles directly, so skipping this leaves a real
    # user stuck with an empty roles list despite a correct DB row.
    # Confirmed by a real run: UAT's testuser had user_roles = ['Coach']
    # but /me still returned roles: [] until this group was added.
    idp.admin_add_user_to_group(
        UserPoolId=settings.cognito_user_pool_id,
        Username=user.email,
        GroupName=GROUP_BY_ROLE[user.role],
    )

    return f"{user.email} -> profile {profile.id}, role={user.role} ({'granted' if granted else 'already held'})"


async def main() -> None:
    _guard()
    idp = admin_cognito()
    engine = create_async_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            for user in USERS:
                line = await _provision_one(session, idp, user)
                print(line)
            await session.commit()
    finally:
        await engine.dispose()
    print(f"\nProvisioned {len(USERS)} user(s) against {settings.environment}.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # noqa: BLE001 -- a one-off ops script: surface any failure plainly and fail the pipeline
        print(f"::error::{exc}", file=sys.stderr)
        sys.exit(1)
