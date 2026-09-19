"""Factory helpers: converge-style creators for kernel rows, builders for reference rows.

The kernel creators are **idempotent upserts** keyed on natural keys (profile
email, role/programme name) — calling twice yields one row, updated in place.
The reference builders return unsaved ORM instances with sensible defaults for
tests; the real reference dataset is produced by ``strata.engine``'s ingest
pipelines (the domain's writer), not by fixtures.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from strata_core.db.common import new_id
from strata_core.domains.clinical import (
    MemberDemographics,
    MemberDiagnosis,
    MemberMedication,
    MemberProcedure,
)
from strata_core.domains.kernel import (
    COGNITO_SUB,
    DEFAULT_PROFILE_TIMEZONE,
    EXTERNAL_ID_TYPE_IDS,
    LANGUAGE_CODES,
    ROLE_IDS,
    Role,
    UserExternalId,
    UserLanguage,
    UserProfile,
    UserRole,
)
from strata_core.domains.reference import Diagnosis, Procedure, Product


async def create_profile(
    session: AsyncSession,
    *,
    email: str | None,
    first_name: str,
    last_name: str,
    display_name: str | None = None,
    cognito_sub: str | None = None,
    timezone: str = DEFAULT_PROFILE_TIMEZONE,
    languages: tuple[str, ...] = ("en",),
    preferred_language: str | None = None,
    profile_id: str | None = None,
) -> UserProfile:
    """Upsert a person by email (the cast's identity key) and converge their fields.

    ``profile_id`` fixes the id on first creation (deterministic datasets);
    an existing row keeps its id — the email is the identity key. Emails are
    deliberately NOT unique (Cognito may reuse one after pool recreation), so a
    duplicate-email state converges the EARLIEST row rather than crashing. An
    email-less person (— e.g. the means-test member) must carry a fixed
    ``profile_id`` to converge on instead.

    ``first_name``/``last_name`` are required and non-blank;
    ``display_name`` is the optional presentation override — omitted
    means the person renders as "First Last".

    ``languages`` converges the ``user_languages`` links; ``preferred_language``
    (when given) must be one of them — the service-level rule, honoured in dev data.

    ``cognito_sub`` keeps its name but the column moved to ``kernel.identifiers``:
    when given, the profile is guaranteed an active ``Cognito Sub``
    mapping — an existing active mapping wins (the seed's real pool subject is
    never reverted to a fixture placeholder); otherwise the value is inserted.
    """
    if not first_name.strip() or not last_name.strip():
        raise ValueError("first_name and last_name must be non-blank ")
    unknown = set(languages) - set(LANGUAGE_CODES)
    if unknown:
        raise ValueError(
            f"Unknown language codes {sorted(unknown)}; expected among {LANGUAGE_CODES}"
        )
    if preferred_language is not None and preferred_language not in languages:
        raise ValueError(
            f"preferred_language {preferred_language!r} is not among languages {languages}"
        )
    if email is None and not profile_id:
        raise ValueError("an email-less profile needs a fixed profile_id to converge on ")
    if email is not None:
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
    else:
        profile = await session.get(UserProfile, profile_id)
    if profile is None:
        profile = UserProfile(email=email)
        if profile_id:
            profile.id = profile_id
        session.add(profile)
    # Names converge on the same lines for new and existing profiles alike.
    profile.first_name = first_name
    profile.last_name = last_name
    profile.display_name = display_name
    profile.timezone = timezone
    profile.preferred_language_code = preferred_language
    await session.flush()
    if cognito_sub is not None:
        await _ensure_cognito_mapping(session, user_id=profile.id, cognito_sub=cognito_sub)
    await _converge_languages(session, profile, languages)
    return profile


async def _ensure_cognito_mapping(session: AsyncSession, *, user_id: str, cognito_sub: str) -> None:
    """Guarantee ``user_id`` holds an active ``Cognito Sub`` mapping.

    An existing active mapping wins regardless of value — mirroring the old
    column semantics, where re-running a fixture never overwrote an adopted
    real pool subject with the placeholder. Only a mapping-less profile gets
    the given value inserted.
    """
    type_id = EXTERNAL_ID_TYPE_IDS[COGNITO_SUB]
    active = (
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
        .first()
    )
    if active is not None:
        return
    session.add(
        UserExternalId(
            user_id=user_id,
            type_id=type_id,
            external_id=cognito_sub,
            registered_at=datetime.now(UTC),
        )
    )
    await session.flush()


async def _converge_languages(
    session: AsyncSession, profile: UserProfile, codes: tuple[str, ...]
) -> None:
    """Converge ``user_languages`` to exactly ``codes`` — add missing, drop stale."""
    rows = (
        (await session.execute(select(UserLanguage).where(UserLanguage.user_id == profile.id)))
        .scalars()
        .all()
    )
    current = {row.language_code: row for row in rows}
    for code in codes:
        if code not in current:
            await session.execute(
                pg_insert(UserLanguage)
                .values(id=new_id(), user_id=profile.id, language_code=code)
                .on_conflict_do_nothing(index_elements=["user_id", "language_code"])
            )
    for code, row in current.items():
        if code not in codes:
            await session.delete(row)
    await session.flush()


async def assign_role(session: AsyncSession, profile: UserProfile, role_name: str) -> None:
    """Idempotently assign a catalogue role to a profile (race-safe, name-validated)."""
    if role_name not in ROLE_IDS:
        raise ValueError(f"Unknown role {role_name!r}; expected one of {sorted(ROLE_IDS)}")
    await session.execute(
        pg_insert(UserRole)
        .values(id=new_id(), user_id=profile.id, role_id=ROLE_IDS[role_name])
        .on_conflict_do_nothing(index_elements=["user_id", "role_id"])
    )
    await session.flush()


async def converge_roles(
    session: AsyncSession, profile: UserProfile, role_names: tuple[str, ...]
) -> None:
    """Converge assignments to exactly ``role_names`` — add missing, revoke stale."""
    rows = (
        (
            await session.execute(
                select(UserRole, Role.name)
                .join(Role, UserRole.role_id == Role.id)
                .where(UserRole.user_id == profile.id)
            )
        )
        .tuples()
        .all()
    )
    current = {name: user_role for user_role, name in rows}
    for name in role_names:
        if name not in current:
            await assign_role(session, profile, name)
    for name, user_role in current.items():
        if name not in role_names:
            await session.delete(user_role)
    await session.flush()


async def converge_diagnosis(
    session: AsyncSession,
    *,
    row_id: str,
    member_user_id: str,
    code: str,
    starts_on: date | None = None,
    ends_on: date | None = None,
) -> MemberDiagnosis:
    """Converge a member-diagnosis link on its fixed ``row_id``.

    The clinical link tables have **no natural key by design** (recurrence
    is multiple rows for the same code), so deterministic fixtures key on an
    explicit row id and converge the fields in place.
    """
    row = await session.get(MemberDiagnosis, row_id)
    if row is None:
        row = MemberDiagnosis(id=row_id)
        session.add(row)
    row.member_user_id, row.code = member_user_id, code
    row.starts_on, row.ends_on = starts_on, ends_on
    await session.flush()
    return row


async def converge_procedure(
    session: AsyncSession,
    *,
    row_id: str,
    member_user_id: str,
    code: str,
    starts_on: date | None = None,
    ends_on: date | None = None,
    status: str | None = None,
) -> MemberProcedure:
    """Converge a member-procedure link on its fixed ``row_id`` (see
    ``converge_diagnosis`` for why the key is explicit)."""
    row = await session.get(MemberProcedure, row_id)
    if row is None:
        row = MemberProcedure(id=row_id)
        session.add(row)
    row.member_user_id, row.code, row.status = member_user_id, code, status
    row.starts_on, row.ends_on = starts_on, ends_on
    await session.flush()
    return row


async def converge_medication(
    session: AsyncSession,
    *,
    row_id: str,
    member_user_id: str,
    product_id: str,
    note: str | None = None,
    starts_on: date | None = None,
    ends_on: date | None = None,
) -> MemberMedication:
    """Converge a member-medication link on its fixed ``row_id`` (see
    ``converge_diagnosis`` for why the key is explicit)."""
    row = await session.get(MemberMedication, row_id)
    if row is None:
        row = MemberMedication(id=row_id)
        session.add(row)
    row.member_user_id, row.product_id, row.note = member_user_id, product_id, note
    row.starts_on, row.ends_on = starts_on, ends_on
    await session.flush()
    return row


async def converge_demographics(
    session: AsyncSession,
    *,
    member_user_id: str,
    sex: str | None = None,
    date_of_birth: date | None = None,
    ethnicity: str | None = None,
    marital_status: str | None = None,
    height_in: Decimal | None = None,
) -> MemberDemographics:
    """Converge the one-to-one demographics row on its ``member_user_id`` PK."""
    row = await session.get(MemberDemographics, member_user_id)
    if row is None:
        row = MemberDemographics(member_user_id=member_user_id)
        session.add(row)
    row.sex, row.date_of_birth = sex, date_of_birth
    row.ethnicity, row.marital_status, row.height_in = ethnicity, marital_status, height_in
    await session.flush()
    return row


async def register_device(
    session: AsyncSession,
    *,
    user_id: str,
    type_name: str,
    external_id: str,
    registered_at: datetime,
    registration_id: str | None = None,
) -> UserExternalId:
    """Converge the active ``(type, external id)`` mapping onto ``user_id``.

    Mirrors the registration API's supersede-for-audit semantics:
    an active mapping to another user is **ended** at ``registered_at``, never
    deleted; an active mapping to the same user is kept as-is (idempotent).
    ``registration_id`` fixes the id on first creation (deterministic datasets)
    and is skipped if that id already exists — e.g. as an ended history row
    after a supersede round-trip.
    """
    if type_name not in EXTERNAL_ID_TYPE_IDS:
        raise ValueError(
            f"Unknown external-id type {type_name!r}; expected one of "
            f"{sorted(EXTERNAL_ID_TYPE_IDS)}"
        )
    type_id = EXTERNAL_ID_TYPE_IDS[type_name]
    active = (
        await session.execute(
            select(UserExternalId).where(
                UserExternalId.type_id == type_id,
                UserExternalId.external_id == external_id,
                UserExternalId.ended_at.is_(None),
            )
        )
    ).scalar_one_or_none()  # at most one live mapping (uq_user_external_ids_active)
    if active is not None:
        if active.user_id == user_id:
            return active
        active.ended_at = registered_at
        # The end must reach the database before the replacement row is inserted, or
        # the one-live-mapping partial unique index rejects the insert.
        await session.flush()
    row = UserExternalId(
        user_id=user_id,
        type_id=type_id,
        external_id=external_id,
        registered_at=registered_at,
    )
    if registration_id and await session.get(UserExternalId, registration_id) is None:
        row.id = registration_id
    session.add(row)
    await session.flush()
    return row


def build_product(**overrides: Any) -> Product:
    """An unsaved Product with the minimum viable shape (tests fill the rest)."""
    values: dict[str, Any] = {
        "product_id": "0000-0000_test",
        "product_ndc": "0000-0000",
        "brand_name": "Testodrol",
        "generic_name": "testosterone",
        "raw": {},
    }
    values.update(overrides)
    return Product(**values)


def build_diagnosis(**overrides: Any) -> Diagnosis:
    values: dict[str, Any] = {
        "code": "G932",
        "short_title": "Benign IH",
        "long_title": "Benign intracranial hypertension",
        "order_number": 1,
    }
    values.update(overrides)
    return Diagnosis(**values)


def build_procedure(**overrides: Any) -> Procedure:
    values: dict[str, Any] = {
        "code": "0016070",
        "short_title": "Bypass Ventricle",
        "long_title": "Bypass Cerebral Ventricle to Nasopharynx",
        "order_number": 1,
    }
    values.update(overrides)
    return Procedure(**values)
