"""The identifier seam — typed external identities on ``kernel.identifiers``.

The third leg of the identity seam, beside :mod:`strata_identity.profiles`
(profiles) and :mod:`strata_identity.roles` (roles): session-taking functions
the consumer calls with its own ``AsyncSession``; the models live in
``strata-core``. This module IS the identity-owned write path the ownership
registry names ``strata.engine.auth`` as writer of: every mapping write
— Cognito adoption, device registration (Strata.Connect delegates here,
Identity), the legacy import — goes through it.

Semantics (promoted from Strata.Connect's proven registration logic):

- **Supersede, never delete**: an active mapping that must
  give way gains ``ended_at``; history is append-and-end.
- **Idempotent re-adopt**: mapping a value to the user who already actively
  holds it returns the existing row unchanged (at-least-once callers must not
  flip-flop the audit trail).
- **Race backstop**: the partial unique index on active (type, value) —
  ``uq_user_external_ids_active`` — rejects concurrent duplicate actives; the
  caller's transaction fails and retries cleanly.
- **Exclusive types**: a person holds at most one active ``Cognito Sub``
  (one-identity-per-person); ``exclusive=True`` ends the user's other active
  mappings of the type in the same act. Device types are NOT exclusive — a
  member may hold several scales — so device callers leave the flag off.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.kernel import (
    COGNITO_SUB,
    EXTERNAL_ID_TYPE_IDS,
    UserExternalId,
    UserProfile,
)

from strata_identity.security import Principal

_log = logging.getLogger("strata_identity.identifiers")


class UnknownIdentifierTypeError(LookupError):
    """The named identifier type is not in the catalogue (a closed list)."""


class UnmappedSubjectError(LookupError):
    """A verified subject that resolves to no profile — reject the request.

    Raised, never silently provisioned: every legitimate mapping has a
    controlled origin (the import, migration login, admin onboarding, or the
    dev seed), so an unresolvable subject is an anomaly the service
    surfaces as an authentication failure.
    """


def _type_id(type_name: str) -> str:
    try:
        return EXTERNAL_ID_TYPE_IDS[type_name]
    except KeyError:
        raise UnknownIdentifierTypeError(
            f"Unknown identifier type {type_name!r}; expected one of {sorted(EXTERNAL_ID_TYPE_IDS)}"
        ) from None


async def resolve_profile(
    session: AsyncSession, *, type_name: str, external_id: str
) -> UserProfile | None:
    """The profile holding the ACTIVE mapping for (``type_name``, ``external_id``)."""
    result = await session.execute(
        select(UserProfile)
        .join(UserExternalId, UserExternalId.user_id == UserProfile.id)
        .where(
            UserExternalId.type_id == _type_id(type_name),
            UserExternalId.external_id == external_id,
            UserExternalId.ended_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def active_mapping(
    session: AsyncSession, *, type_name: str, external_id: str
) -> UserExternalId | None:
    """The single ACTIVE mapping row for (``type_name``, ``external_id``), if any."""
    return (
        await session.execute(
            select(UserExternalId).where(
                UserExternalId.type_id == _type_id(type_name),
                UserExternalId.external_id == external_id,
                UserExternalId.ended_at.is_(None),
            )
        )
    ).scalar_one_or_none()  # at most one live mapping (uq_user_external_ids_active)


async def active_mappings_for_user(
    session: AsyncSession, *, user_id: str, type_name: str
) -> list[UserExternalId]:
    """The user's ACTIVE mappings of ``type_name`` (device types may hold several;
    exclusive types hold at most one by ``adopt(exclusive=True)``'s convention).

    The migration login's gate reads this (Identity): a profile
    about to be migrated must hold NO active ``Cognito Sub`` mapping — one
    already present means the unknown-user signal and the database disagree,
    and the login fails hard rather than supersede on a guess.
    """
    return list(
        (
            await session.execute(
                select(UserExternalId).where(
                    UserExternalId.user_id == user_id,
                    UserExternalId.type_id == _type_id(type_name),
                    UserExternalId.ended_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )


async def adopt(
    session: AsyncSession,
    *,
    user_id: str,
    type_name: str,
    external_id: str,
    registered_at: datetime | None = None,
    exclusive: bool = False,
) -> UserExternalId:
    """Converge the active (``type_name``, ``external_id``) mapping onto ``user_id``.

    Flushes but does not commit — the caller owns the transaction. An active
    mapping already held by ``user_id`` is returned unchanged (idempotent); one
    held by another user is ended at ``registered_at`` (supersede-for-audit).
    ``exclusive=True`` additionally ends the user's OTHER active mappings of
    this type (the one-identity-per-person types: ``Cognito Sub``); device
    callers leave it off — a member may hold several devices of one type.
    """
    now = registered_at or datetime.now(UTC)
    type_id = _type_id(type_name)

    active = await active_mapping(session, type_name=type_name, external_id=external_id)
    if active is not None and active.user_id == user_id:
        return active
    if active is not None:
        active.ended_at = now  # ended, not deleted — the audit trail

    if exclusive:
        others = (
            (
                await session.execute(
                    select(UserExternalId).where(
                        UserExternalId.user_id == user_id,
                        UserExternalId.type_id == type_id,
                        UserExternalId.ended_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in others:
            row.ended_at = now

    # Ends must reach the database before the replacement row is inserted, or the
    # one-live-mapping partial unique index rejects the insert.
    await session.flush()
    mapping = UserExternalId(
        user_id=user_id, type_id=type_id, external_id=external_id, registered_at=now
    )
    session.add(mapping)
    await session.flush()
    _log.info("mapping adopted: type=%s user=%s", type_name, user_id)
    return mapping


async def end_mapping(
    session: AsyncSession,
    *,
    type_name: str,
    external_id: str,
    ended_at: datetime | None = None,
) -> UserExternalId | None:
    """End the active (``type_name``, ``external_id``) mapping, keeping history.

    Flushes but does not commit. Returns the ended row, or None when no active
    mapping exists (idempotent — ending twice is a no-op).
    """
    active = await active_mapping(session, type_name=type_name, external_id=external_id)
    if active is None:
        return None
    active.ended_at = ended_at or datetime.now(UTC)
    await session.flush()
    return active


async def resolve_principal(session: AsyncSession, principal: Principal) -> UserProfile:
    """Resolve a verified principal to its kernel profile — the ONE resolution rule.

    Mapping-first (the canonical path: real pool tokens carry the Cognito
    subject, held as an active ``Cognito Sub`` mapping), then the profile id
    itself (dev tokens are historically minted with sub = the profile id —
    harmless in cognito mode, where pool UUIDs never collide with profile
    ids), and **fail hard** when neither resolves: provisioning is
    never implicit. Promoted from Strata.Booking's ``_domain_principal``.
    """
    profile = await resolve_profile(session, type_name=COGNITO_SUB, external_id=principal.sub)
    if profile is None:
        profile = await session.get(UserProfile, principal.sub)
    if profile is None:
        # the subject is a catalogued identifier value — never in a log
        # line or exception message (the caller's correlation ID is the rider).
        _log.warning("unresolvable subject rejected (no implicit provisioning)")
        raise UnmappedSubjectError(
            "no profile resolvable for this subject (no implicit provisioning)"
        )
    return profile
