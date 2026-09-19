"""The in-service migration login.

Entered ONLY on ``UserNotFoundException`` — distinct from a wrong password under
``PreventUserExistenceErrors: LEGACY``, so an existing user's failed
sign-in never consults the legacy platform and an adopted
user's successful one never comes near this module. The sequence:

    legacy credentials check → gate (the imported ``Legacy Summit Django User
    ID`` mapping resolves exactly one profile with NO active ``Cognito Sub``
    mapping, plus the identifier/kind/internal-id cross-checks)
    → local pool-policy check on the
    password the account will be created with (failure → the upgrade
    signal, — nothing created) → AdminCreateUser (invite suppressed)
    + AdminSetUserPassword (permanent) + AdminAddUserToGroup per imported role
    (cognito:groups carries the coarse roles) → adopt + commit
    (failure anywhere after creation → compensating AdminDeleteUser,
) → the caller retries the sign-in deterministically.

Every failure raises ``MigrationLoginFailed`` naming its leg for the log line;
the route collapses ALL of them to the one generic invalid-credentials response.
The single exception is ``PasswordUpgradeRequired``: raised
only AFTER legacy acceptance and a fully passed gate, so the distinct response
it becomes is reachable solely with proven-valid credentials for a migratable
account — everyone else stays inside the generic collapse.

Cognito usernames must never be email-format (the alias pool rejects
them), so an email identifier gets the profile id as its synthetic username and
keeps the email as a verified attribute — the alias then serves their sign-ins.
Log lines carry the correlation id (middleware-bound) and non-sensitive leg
names only: no credentials, tokens, or identifier values, ever.

A concurrent first login can win the ``AdminCreateUser`` race
(``UsernameExistsException``): absorbed by returning normally — the caller's
retry signs in against the winner's account.
"""

from functools import partial

import anyio
import structlog
from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.kernel import (
    COGNITO_SUB,
    LEGACY_SUMMIT_COACH_ID,
    LEGACY_SUMMIT_DJANGO_USER_ID,
    LEGACY_SUMMIT_PATIENT_ID,
    UserProfile,
)
from strata_identity.identifiers import active_mappings_for_user, adopt, resolve_profile
from strata_identity.roles import GROUP_BY_ROLE, ROLE_MEMBER, roles_for

from strata_engine_auth.core.config import settings
from strata_engine_auth.core.passwords import validate_password_policy
from strata_engine_auth.services import cognito
from strata_engine_auth.services.legacy import (
    LegacyCredentialsClient,
    LegacyUnavailableError,
)

_log = structlog.get_logger("strata_engine_auth.migration")


class MigrationLoginFailed(Exception):
    """One outcome for every failure leg; ``leg`` names it for the log line only —
    nothing sensitive rides here, and the client sees the same generic error
    regardless."""

    def __init__(self, leg: str) -> None:
        super().__init__(leg)
        self.leg = leg


class PasswordUpgradeRequired(Exception):
    """legacy accepted + gate passed, but the creation password fails the
    pool policy. A SIBLING of ``MigrationLoginFailed``, never a subclass — the
    route's generic collapse must not swallow it. Carries nothing but the drift
    flag: WHICH requirements went unmet is a property of a live credential and
    never reaches a log line."""

    def __init__(self, *, drift: bool = False) -> None:
        super().__init__("password_upgrade_required")
        self.drift = drift


async def migrate(
    session: AsyncSession,
    legacy: LegacyCredentialsClient,
    identifier: str,
    password: str,
    *,
    creation_password: str | None = None,
) -> None:
    """Ensure the migrating user's Cognito account + mapping exist, or raise.

    ``password`` is what the legacy platform verifies; the account is created
    with ``creation_password`` when given (the upgrade path) and with
    ``password`` itself otherwise (— preserved). Returns normally
    when the caller should retry the sign-in — after a successful migration, or
    after absorbing a concurrent first login's ``UsernameExistsException`` (the
    winner's account serves the retry).
    """
    try:
        verdict = await legacy.check(identifier, password)
    except LegacyUnavailableError:
        raise MigrationLoginFailed("legacy_unavailable") from None
    if not verdict.accepted or verdict.legacy_user_id is None:
        raise MigrationLoginFailed("legacy_rejected")  # /c — reveal nothing

    # The gate. The Legacy ID mapping is unique-active by
    # index, so "zero or multiple candidates" reduces to: no imported mapping
    # (unmigrated → generic, 4.1c), or a profile whose state contradicts the
    # unknown-user signal / the typed identifier — anomalies that fail hard.
    profile = await resolve_profile(
        session, type_name=LEGACY_SUMMIT_DJANGO_USER_ID, external_id=verdict.legacy_user_id
    )
    if profile is None:
        raise MigrationLoginFailed("no_imported_mapping")
    # Serialize concurrent first logins of the SAME profile (e.g. one by app
    # username, one by email, on two devices): lock the profile row so a second
    # attempt blocks until the first commits its Cognito Sub mapping, then reads
    # it just below and fails hard as an already-adopted anomaly instead of
    # creating a second pool account. (Concurrency at scale is a later phase;
    # this closes the review's same-profile double-create — review finding.)
    await session.execute(
        select(UserProfile.id).where(UserProfile.id == profile.id).with_for_update()
    )
    if await active_mappings_for_user(session, user_id=profile.id, type_name=COGNITO_SUB):
        # Adopted already, yet Cognito said unknown user: pool/database drift.
        # Never supersede on a guess — fail hard, leave state for inspection.
        raise MigrationLoginFailed("already_adopted_anomaly")
    email_identifier = "@" in identifier
    if email_identifier and (profile.email or "").lower() != identifier.lower():
        raise MigrationLoginFailed("identifier_profile_mismatch")
    roles = await roles_for(session, profile.id)
    if verdict.kind == "patient" and ROLE_MEMBER not in roles:
        # The kind discriminator ( contract) contradicts the imported
        # roles — defence in depth against migrating a mislabelled account.
        raise MigrationLoginFailed("kind_profile_mismatch")
    if verdict.kind is not None:
        # The response's internal patient/coach id must match
        # the imported Legacy Summit Patient/Coach ID mapping exactly — the
        # import always writes it, so zero or a different value is an anomaly.
        internal_type = (
            LEGACY_SUMMIT_PATIENT_ID if verdict.kind == "patient" else LEGACY_SUMMIT_COACH_ID
        )
        held = await active_mappings_for_user(session, user_id=profile.id, type_name=internal_type)
        if [mapping.external_id for mapping in held] != [verdict.internal_id]:
            raise MigrationLoginFailed("internal_id_profile_mismatch")

    # / the password the ACCOUNT gets (not necessarily the one
    # legacy verified) must meet the pool policy. Checked here — after legacy
    # acceptance and the complete gate, before anything is created — so the
    # distinct signal is reachable only by a proven-migratable caller and an
    # abandoned upgrade screen costs nothing.
    new_password = password if creation_password is None else creation_password
    if validate_password_policy(new_password, settings):
        raise PasswordUpgradeRequired()

    # Preserve the typed identifier as the username — except an
    # email, which an alias pool rejects as a username: the profile id stands in
    # (deterministic, unique, never email-format) and the verified email
    # attribute keeps the typed identifier working via the alias.
    username = profile.id if email_identifier else identifier
    try:
        sub = await anyio.to_thread.run_sync(
            partial(cognito.admin_create_user, username, profile.email)
        )
    except ClientError as err:
        if err.response["Error"]["Code"] == "UsernameExistsException":
            _log.info("migration_race_absorbed")  # a concurrent first login won
            return
        raise MigrationLoginFailed("admin_create_failed") from None
    except BotoCoreError:
        # The ADMIN side failed before any API call — an expired developer AWS
        # session, a missing IAM role (found live at the walkthrough). Not a
        # ClientError, so name its own leg; nothing was created.
        raise MigrationLoginFailed("admin_channel_unavailable") from None

    # The window between AdminCreateUser and the committed mapping: the typed
    # handlers below compensate their own known failures and raise a normal
    # exception, which the outer clause re-raises untouched. The outer
    # BaseException net exists for what they can't model — above all
    # asyncio.CancelledError from a client disconnect or shutdown (a
    # BaseException, so it slips past every `except Exception`): it must still
    # delete the orphaned account, then re-raise to honour the cancel.
    try:
        try:
            await anyio.to_thread.run_sync(
                partial(cognito.admin_set_permanent_password, username, new_password)
            )
        except ClientError as err:
            # Never leave a passwordless account behind (the discipline).
            await _compensate_delete(username)
            if err.response["Error"]["Code"] == "InvalidPasswordException":
                # The local policy mirror drifted laxer than the real pool: the
                # remedy for the CALLER is still a stronger password; ops fix the
                # Settings mirror (config.py) off this warning.
                _log.warning("password_policy_drift")
                raise PasswordUpgradeRequired(drift=True) from None
            raise MigrationLoginFailed("set_password_failed_compensated") from None
        except BotoCoreError:
            await _compensate_delete(username)
            raise MigrationLoginFailed("admin_channel_unavailable") from None

        # Mirror the imported roles onto the pool groups BEFORE the caller's retry
        # mints tokens — coarse roles ride as cognito:groups, so a user
        # migrated without groups would sign in role-less (found live at).
        try:
            for group in sorted(GROUP_BY_ROLE[role] for role in roles if role in GROUP_BY_ROLE):
                await anyio.to_thread.run_sync(
                    partial(cognito.admin_add_user_to_group, username, group)
                )
        except ClientError:
            await _compensate_delete(username)
            raise MigrationLoginFailed("group_assignment_failed_compensated") from None
        except BotoCoreError:
            await _compensate_delete(username)
            raise MigrationLoginFailed("admin_channel_unavailable") from None

        try:
            await adopt(
                session,
                user_id=profile.id,
                type_name=COGNITO_SUB,
                external_id=sub,
                exclusive=True,
            )
            await session.commit()
        except Exception:
            # / never leave a Cognito account without its mapping
            # delete it so the next attempt migrates again from a clean state.
            await session.rollback()
            await _compensate_delete(username)
            raise MigrationLoginFailed("adoption_failed_compensated") from None
    except (MigrationLoginFailed, PasswordUpgradeRequired):
        raise  # a typed handler above already compensated
    except BaseException:
        # Cancellation or any failure the typed handlers didn't model: the
        # account exists but is not yet adopted — compensate, then re-raise.
        await _compensate_delete(username)
        raise
    _log.info("migration_login_adopted")


async def _compensate_delete(username: str) -> None:
    """Best-effort compensation. If the delete itself fails (the admin
    channel died mid-flow), the half-created account is logged for ops — the
    caller still collapses to the generic failure, never a 500. Until an
    operator deletes the account, that user's retries fail generically (the
    account exists but holds no password)."""
    try:
        await anyio.to_thread.run_sync(partial(cognito.admin_delete_user, username))
    except Exception:
        _log.warning("migration_compensation_failed")
