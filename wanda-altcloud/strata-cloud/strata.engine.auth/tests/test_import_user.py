"""The one-user legacy import: the "imported" state, the
Legacy Summit identifier split (both ids stored), idempotent
convergence (the pre-split backfill path), and the full rehearsal — import,
then first login migrates (the live UAT walkthrough's exact shape, minus the
live endpoints).
"""

from typing import Any

import pytest
from conftest import run_db
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from strata_core.domains.kernel import (
    COGNITO_SUB,
    EXTERNAL_ID_TYPE_IDS,
    LEGACY_SUMMIT_COACH_ID,
    LEGACY_SUMMIT_DJANGO_USER_ID,
    LEGACY_SUMMIT_PATIENT_ID,
    UserExternalId,
    UserProfile,
)
from strata_identity.roles import roles_for
from test_migration_login import (
    PASSWORD,
    FakePool,
    StubLegacy,
    _active_sub,
    _import_user,
    _login,
    _use_legacy,
    accepted,
)

from scripts.import_user import import_user
from strata_engine_auth.services import cognito

pytestmark = pytest.mark.integration


@pytest.fixture
def pool(monkeypatch: pytest.MonkeyPatch) -> FakePool:
    """Same fake as the migration suite's (fixtures don't travel via import)."""
    fake = FakePool()
    monkeypatch.setattr(cognito, "login", fake.login)
    monkeypatch.setattr(cognito, "admin_create_user", fake.admin_create_user)
    monkeypatch.setattr(cognito, "admin_set_permanent_password", fake.admin_set_permanent_password)
    monkeypatch.setattr(cognito, "admin_add_user_to_group", fake.admin_add_user_to_group)
    monkeypatch.setattr(cognito, "admin_delete_user", fake.admin_delete_user)
    return fake


async def _import(
    sessionmaker: async_sessionmaker[AsyncSession], **kwargs: Any
) -> tuple[str, bool]:
    async with sessionmaker() as session:
        result = await import_user(session, **kwargs)
        await session.commit()
        return result


async def _mappings(
    sessionmaker: async_sessionmaker[AsyncSession], profile_id: str, type_name: str
) -> list[str]:
    async with sessionmaker() as session:
        return list(
            (
                await session.execute(
                    select(UserExternalId.external_id).where(
                        UserExternalId.user_id == profile_id,
                        UserExternalId.type_id == EXTERNAL_ID_TYPE_IDS[type_name],
                        UserExternalId.ended_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )


def test_patient_import_creates_the_imported_state(db_sessionmaker: Any) -> None:
    """Patient import: profile + Member role + member detail + BOTH active legacy
    mappings (Django user id AND the patient internal id) — no email
    required, NO Cognito anything (the migration login does that at first sign-in)."""
    profile_id, created = run_db(
        _import(
            db_sessionmaker,
            django_id="2403",
            internal_id="1688",
            kind="patient",
            first_name="Test",
            last_name="One",
            preferred_language="en",
        )
    )
    assert created
    assert run_db(_mappings(db_sessionmaker, profile_id, LEGACY_SUMMIT_DJANGO_USER_ID)) == ["2403"]
    assert run_db(_mappings(db_sessionmaker, profile_id, LEGACY_SUMMIT_PATIENT_ID)) == ["1688"]
    assert run_db(_mappings(db_sessionmaker, profile_id, LEGACY_SUMMIT_COACH_ID)) == []
    assert run_db(_mappings(db_sessionmaker, profile_id, COGNITO_SUB)) == []

    async def _preferred_and_roles() -> tuple[str | None, list[str]]:
        async with db_sessionmaker() as session:
            profile = await session.get(UserProfile, profile_id)
            return (
                profile.preferred_language_code if profile else None,
                await roles_for(session, profile_id),
            )

    preferred, roles = run_db(_preferred_and_roles())
    assert roles == ["Member"]  # membership lives in user_roles (member_details dropped)
    assert preferred == "en"  # person-level preferred language


def test_coach_import_requires_an_email(db_sessionmaker: Any) -> None:
    """The staff email is a coach's login identifier and the gate cross-checks it."""
    with pytest.raises(ValueError, match="requires --email"):
        run_db(
            _import(
                db_sessionmaker,
                django_id="2507",
                internal_id="743",
                kind="coach",
                first_name="Erl",
                last_name="Range",
            )
        )


def test_coach_import_carries_the_email_role_and_coach_id(db_sessionmaker: Any) -> None:
    """A coach import stores the staff email, the Coach role, and the Legacy Summit Coach ID
    mapping."""
    profile_id, created = run_db(
        _import(
            db_sessionmaker,
            django_id="2507",
            internal_id="743",
            kind="coach",
            first_name="Erl",
            last_name="Range",
            email="erlrange@example.com",
        )
    )
    assert created
    assert run_db(_mappings(db_sessionmaker, profile_id, LEGACY_SUMMIT_COACH_ID)) == ["743"]
    assert run_db(_mappings(db_sessionmaker, profile_id, LEGACY_SUMMIT_PATIENT_ID)) == []

    async def _check() -> tuple[str | None, list[str]]:
        from strata_core.domains.kernel import UserProfile

        async with db_sessionmaker() as session:
            profile = await session.get(UserProfile, profile_id)
            assert profile is not None
            return profile.email, await roles_for(session, profile_id)

    email, roles = run_db(_check())
    assert (email, roles) == ("erlrange@example.com", ["Coach"])


def test_import_is_idempotent(db_sessionmaker: Any) -> None:
    """Re-running reports the existing profile untouched — no duplicates,
    no audit flip-flop (the seed's convergence precedent)."""
    first_id, first_created = run_db(
        _import(
            db_sessionmaker,
            django_id="2200",
            internal_id="1100",
            kind="patient",
            first_name="Once",
            last_name="Once",
        )
    )
    again_id, again_created = run_db(
        _import(
            db_sessionmaker,
            django_id="2200",
            internal_id="1100",
            kind="patient",
            first_name="Twice",
            last_name="Twice",
        )
    )
    assert (first_created, again_created) == (True, False)
    assert again_id == first_id
    assert run_db(_mappings(db_sessionmaker, first_id, LEGACY_SUMMIT_DJANGO_USER_ID)) == ["2200"]
    assert run_db(_mappings(db_sessionmaker, first_id, LEGACY_SUMMIT_PATIENT_ID)) == ["1100"]


def test_rerun_backfills_a_missing_internal_mapping(db_sessionmaker: Any) -> None:
    """The convergence path: a pre-split import (Django-user-id row only)
    gains its internal-id mapping on re-run — the official backfill."""
    run_db(
        _import_user(
            db_sessionmaker,
            profile_id="pre-split",
            legacy_id="2300",
            email=None,
            with_internal=False,
        )
    )
    profile_id, created = run_db(
        _import(
            db_sessionmaker,
            django_id="2300",
            internal_id="1300",
            kind="patient",
            first_name="Converged",
            last_name="Converged",
        )
    )
    assert (profile_id, created) == ("pre-split", False)
    assert run_db(_mappings(db_sessionmaker, "pre-split", LEGACY_SUMMIT_PATIENT_ID)) == ["1300"]


def test_rerun_never_supersedes_a_conflicting_internal_mapping(db_sessionmaker: Any) -> None:
    """A re-run with a conflicting internal id raises and leaves the original mapping held."""
    run_db(
        _import(
            db_sessionmaker,
            django_id="2301",
            internal_id="1301",
            kind="patient",
            first_name="Held",
            last_name="Held",
        )
    )
    with pytest.raises(ValueError, match="never supersedes"):
        run_db(
            _import(
                db_sessionmaker,
                django_id="2301",
                internal_id="9999",
                kind="patient",
                first_name="Held",
                last_name="Held",
            )
        )
    assert run_db(
        _mappings(db_sessionmaker, _first("2301", db_sessionmaker), LEGACY_SUMMIT_PATIENT_ID)
    ) == ["1301"]


def test_import_never_supersedes_another_profiles_internal_mapping(db_sessionmaker: Any) -> None:
    """An internal id actively held by ANOTHER profile fails a fresh import loudly —
    adopt's supersede semantics would silently end the holder's mapping (review finding)."""
    holder_id, _ = run_db(
        _import(
            db_sessionmaker,
            django_id="2600",
            internal_id="7700",
            kind="patient",
            first_name="Holder",
            last_name="Holder",
        )
    )
    with pytest.raises(ValueError, match="never supersedes"):
        run_db(
            _import(
                db_sessionmaker,
                django_id="2601",
                internal_id="7700",
                kind="patient",
                first_name="Typo",
                last_name="Import",
            )
        )
    assert run_db(_mappings(db_sessionmaker, holder_id, LEGACY_SUMMIT_PATIENT_ID)) == ["7700"]


def test_backfill_never_supersedes_another_profiles_internal_mapping(db_sessionmaker: Any) -> None:
    """The convergence backfill refuses an internal id another profile actively holds."""
    holder_id, _ = run_db(
        _import(
            db_sessionmaker,
            django_id="2602",
            internal_id="7801",
            kind="patient",
            first_name="Holder",
            last_name="Holder",
        )
    )
    run_db(
        _import_user(
            db_sessionmaker,
            profile_id="pre-split-two",
            legacy_id="2603",
            email=None,
            with_internal=False,
        )
    )
    with pytest.raises(ValueError, match="never supersedes"):
        run_db(
            _import(
                db_sessionmaker,
                django_id="2603",
                internal_id="7801",
                kind="patient",
                first_name="Converged",
                last_name="Converged",
            )
        )
    assert run_db(_mappings(db_sessionmaker, holder_id, LEGACY_SUMMIT_PATIENT_ID)) == ["7801"]
    assert run_db(_mappings(db_sessionmaker, "pre-split-two", LEGACY_SUMMIT_PATIENT_ID)) == []


def test_rerun_with_the_wrong_kind_is_rejected(db_sessionmaker: Any) -> None:
    """A re-run whose --kind contradicts the imported profile's role fails loudly instead
    of converging a wrong-kind internal mapping onto the profile (review finding)."""
    run_db(
        _import(
            db_sessionmaker,
            django_id="2604",
            internal_id="7900",
            kind="patient",
            first_name="Pat",
            last_name="Pat",
        )
    )
    with pytest.raises(ValueError, match="kind"):
        run_db(
            _import(
                db_sessionmaker,
                django_id="2604",
                internal_id="7901",
                kind="coach",
                first_name="Pat",
                last_name="Pat",
                email="pat@example.com",
            )
        )
    profile_id = _first("2604", db_sessionmaker)
    assert run_db(_mappings(db_sessionmaker, profile_id, LEGACY_SUMMIT_COACH_ID)) == []


def _first(django_id: str, sessionmaker: Any) -> str:
    from strata_identity.identifiers import resolve_profile

    async def inner() -> str:
        async with sessionmaker() as session:
            profile = await resolve_profile(
                session, type_name=LEGACY_SUMMIT_DJANGO_USER_ID, external_id=django_id
            )
            assert profile is not None
            return profile.id

    found: str = run_db(inner())
    return found


def test_imported_patient_migrates_on_first_login(
    db_client: TestClient, app: FastAPI, pool: FakePool, db_sessionmaker: Any
) -> None:
    """The walkthrough's exact shape: the real import script stages the user
    (both ids), the first login runs the whole flow — internal-id cross-check
    included — the second signs in natively."""
    profile_id, _ = run_db(
        _import(
            db_sessionmaker,
            django_id="2403",
            internal_id="1688",
            kind="patient",
            first_name="Test",
            last_name="One",
        )
    )
    _use_legacy(app, StubLegacy(accepted("2403", "patient", internal_id="1688")))

    first = _login(db_client, "test1")
    assert first.status_code == 200, first.text
    assert run_db(_active_sub(db_sessionmaker, profile_id)) == "sub-test1"

    second = _login(db_client, "test1")  # straight Cognito, no branch
    assert second.status_code == 200
    assert pool.users["test1"]["password"] == PASSWORD


def test_import_rejects_blank_names(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Every legacy user carries a first AND last name: a blank
    half fails clean before any write — the kernel's NOT NULL is the backstop,
    never the error message."""
    with pytest.raises(ValueError, match="first and last name"):
        run_db(
            _import(
                db_sessionmaker,
                django_id="9103",
                internal_id="9103",
                kind="patient",
                first_name="Solo",
                last_name="   ",
            )
        )


def test_import_rejects_language_data_the_kernel_would_reject(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Import fails clean (ValueError, before any write) on an unknown catalogue code or a
    preferred language the person does not speak — never a silent inconsistency
    nor an opaque FK error mid-flush."""
    with pytest.raises(ValueError, match="unknown language codes"):
        run_db(
            _import(
                db_sessionmaker,
                django_id="9101",
                internal_id="9101",
                kind="patient",
                first_name="Bad",
                last_name="Lang",
                languages=("zz",),
                preferred_language="zz",
            )
        )
    with pytest.raises(ValueError, match="not among the spoken"):
        run_db(
            _import(
                db_sessionmaker,
                django_id="9102",
                internal_id="9102",
                kind="patient",
                first_name="Bad",
                last_name="Pref",
                languages=("es",),
                preferred_language="en",
            )
        )
