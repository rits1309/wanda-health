"""The migration login: every scenario, the generic
collapse, compensation, the race, and log safety.

No live AWS or network (TESTING.md): Cognito is a ``FakePool`` that mimics the
recreated pool's behaviour — the unknown-user signal (LEGACY), the alias-pool
rejection of email-format usernames, admin create/set-password/delete — and the
legacy platform is a recording stub. DB-touching scenarios run on the ephemeral
migrated Postgres (marked ``integration``).
"""

import asyncio
from typing import Any, Literal

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from conftest import LOGIN_RESULT, run_db
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from strata_core.domains.kernel import (
    COGNITO_SUB,
    EXTERNAL_ID_TYPE_IDS,
    LEGACY_SUMMIT_DJANGO_USER_ID,
    UserExternalId,
    UserProfile,
)
from strata_identity.identifiers import adopt
from strata_identity.roles import assign_role

from strata_engine_auth.api.auth import get_legacy_client
from strata_engine_auth.services import cognito
from strata_engine_auth.services.legacy import LegacyUnavailableError, LegacyVerdict

GENERIC = {"detail": "Invalid credentials"}
PASSWORD = "A-Legacy-Pw!77"
# The pair: a legacy password the pool policy rejects, and the
# compliant replacement the guided upgrade sets.
WEAK_PASSWORD = "legacypw"
NEW_PASSWORD = "A-Stronger-Pw!2026"


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "boom"}}, "InitiateAuth")


class FakePool:
    """The recreated pool's observable behaviour, in miniature."""

    def __init__(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []
        self.hide_next_login = False  # simulates the concurrent-first-login race
        self.reject_next_password = False  # simulates pool-policy drift
        self.cancel_next_password = False  # simulates a client disconnect mid-flow

    def _find(self, identifier: str) -> dict[str, Any] | None:
        if identifier in self.users:
            return self.users[identifier]
        for user in self.users.values():  # the email alias (verified emails only)
            if user["email"] is not None and user["email"].lower() == identifier.lower():
                return user
        return None

    def login(self, identifier: str, password: str) -> dict[str, Any]:
        user = self._find(identifier)
        if user is None or self.hide_next_login:
            self.hide_next_login = False
            raise _client_error("UserNotFoundException")  # LEGACY: distinct
        if user["password"] != password:
            raise _client_error("NotAuthorizedException")
        return LOGIN_RESULT

    def admin_create_user(self, username: str, email: str | None) -> str:
        if "@" in username:  # the alias-pool constraint, enforced like Cognito does
            raise _client_error("InvalidParameterException")
        if username in self.users:
            raise _client_error("UsernameExistsException")
        self.users[username] = {
            "sub": f"sub-{username}",
            "email": email,
            "password": None,
            "groups": set(),
        }
        return f"sub-{username}"

    def admin_add_user_to_group(self, username: str, group: str) -> None:
        self.users[username]["groups"].add(group)

    def admin_set_permanent_password(self, username: str, password: str) -> None:
        if self.cancel_next_password:  # the request was cancelled (client disconnect)
            self.cancel_next_password = False
            raise asyncio.CancelledError()
        if self.reject_next_password:  # the REAL pool's policy said no (drift)
            self.reject_next_password = False
            raise _client_error("InvalidPasswordException")
        self.users[username]["password"] = password

    def admin_delete_user(self, username: str) -> None:
        del self.users[username]
        self.deleted.append(username)


class StubLegacy:
    """A recording legacy platform: one programmed verdict (or outage)."""

    def __init__(self, verdict: LegacyVerdict | None = None, *, unavailable: bool = False) -> None:
        self.verdict = verdict or LegacyVerdict(accepted=False)
        self.unavailable = unavailable
        self.calls: list[str] = []

    async def check(self, username: str, password: str) -> LegacyVerdict:
        self.calls.append(username)
        if self.unavailable:
            raise LegacyUnavailableError("no verdict")
        return self.verdict


def accepted(
    legacy_id: str, kind: Literal["patient", "coach"], internal_id: str | None = None
) -> LegacyVerdict:
    """A live-shaped acceptance; the internal id defaults to the same derived
    value _import_user stages, so verdict and import align by construction."""
    return LegacyVerdict(
        accepted=True,
        legacy_user_id=legacy_id,
        kind=kind,
        internal_id=internal_id or f"int-{legacy_id}",
    )


@pytest.fixture
def pool(monkeypatch: pytest.MonkeyPatch) -> FakePool:
    fake = FakePool()
    monkeypatch.setattr(cognito, "login", fake.login)
    monkeypatch.setattr(cognito, "admin_create_user", fake.admin_create_user)
    monkeypatch.setattr(cognito, "admin_set_permanent_password", fake.admin_set_permanent_password)
    monkeypatch.setattr(cognito, "admin_add_user_to_group", fake.admin_add_user_to_group)
    monkeypatch.setattr(cognito, "admin_delete_user", fake.admin_delete_user)
    return fake


def _use_legacy(app: FastAPI, stub: StubLegacy | None) -> None:
    app.dependency_overrides[get_legacy_client] = lambda: stub


async def _import_user(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    profile_id: str,
    legacy_id: str,
    email: str | None,
    roles: tuple[str, ...] = ("Member",),
    internal_id: str | None = None,
    internal_type: str | None = None,
    with_internal: bool = True,
) -> None:
    """What the /import produces: a profile + roles + the Django-user-id
    AND internal-id mappings — and NO Cognito Sub mapping (the 'imported, not
    yet adopted' state). The internal id defaults to the value
    accepted() derives, so gate cross-checks align by construction."""
    async with sessionmaker() as session:
        session.add(
            UserProfile(
                id=profile_id,
                email=email,
                first_name="Imported",
                last_name=profile_id,
                timezone="Etc/UTC",
            )
        )
        await session.flush()
        for role in roles:
            await assign_role(session, profile_id, role)
        await adopt(
            session,
            user_id=profile_id,
            type_name=LEGACY_SUMMIT_DJANGO_USER_ID,
            external_id=legacy_id,
        )
        if with_internal:
            if internal_type is None:
                internal_type = (
                    "Legacy Summit Coach ID" if "Coach" in roles else "Legacy Summit Patient ID"
                )
            await adopt(
                session,
                user_id=profile_id,
                type_name=internal_type,
                external_id=internal_id or f"int-{legacy_id}",
            )
        await session.commit()


async def _active_sub(sessionmaker: async_sessionmaker[AsyncSession], profile_id: str) -> str:
    async with sessionmaker() as session:
        return (
            await session.execute(
                select(UserExternalId.external_id).where(
                    UserExternalId.user_id == profile_id,
                    UserExternalId.type_id == EXTERNAL_ID_TYPE_IDS[COGNITO_SUB],
                    UserExternalId.ended_at.is_(None),
                )
            )
        ).scalar_one()


def _login(client: TestClient, identifier: str, password: str = PASSWORD) -> Any:
    return client.post("/v1/auth/login", json={"identifier": identifier, "password": password})


# --- the full first-login journeys ------------------------------------


@pytest.mark.integration
def test_first_login_migrates_seamlessly_by_username(
    db_client: TestClient, app: FastAPI, pool: FakePool, db_sessionmaker: Any
) -> None:
    """unknown Cognito user + valid legacy credentials + imported
    Legacy ID → account created with the typed username preserved, mapping
    adopted, and tokens issued from the ONE login attempt."""
    run_db(_import_user(db_sessionmaker, profile_id="m-9", legacy_id="2403", email=None))
    _use_legacy(app, StubLegacy(accepted("2403", "patient")))

    response = _login(db_client, "c149pn2db0b")

    assert response.status_code == 200 and "access_token" in response.json()
    assert pool.users["c149pn2db0b"]["password"] == PASSWORD  # preserved, permanent
    assert pool.users["c149pn2db0b"]["groups"] == {"member"}  # roles ride as groups
    assert run_db(_active_sub(db_sessionmaker, "m-9")) == "sub-c149pn2db0b"


@pytest.mark.integration
def test_first_login_by_email_gets_a_synthetic_username(
    db_client: TestClient, app: FastAPI, pool: FakePool, db_sessionmaker: Any
) -> None:
    """An email identifier cannot become the Cognito username (the alias pool
    rejects it): the profile id stands in and the verified email attribute keeps
    the typed identifier signing in via the alias."""
    run_db(
        _import_user(
            db_sessionmaker,
            profile_id="c-9",
            legacy_id="2507",
            email="coach@wandahealth.com",
            roles=("Coach",),
        )
    )
    _use_legacy(app, StubLegacy(accepted("2507", "coach")))

    response = _login(db_client, "coach@wandahealth.com")

    assert response.status_code == 200
    assert "coach@wandahealth.com" not in pool.users  # never an email-format username
    assert pool.users["c-9"]["email"] == "coach@wandahealth.com"
    assert pool.users["c-9"]["groups"] == {"coach"}
    assert run_db(_active_sub(db_sessionmaker, "c-9")) == "sub-c-9"


# --- the generic collapse (; /c) -------------------------------


def test_wrong_password_never_consults_legacy(
    client: TestClient, app: FastAPI, pool: FakePool
) -> None:
    """/ an EXISTING user's failed sign-in stops at Cognito."""
    pool.users["dana"] = {"sub": "s", "email": None, "password": "right"}
    stub = StubLegacy()
    _use_legacy(app, stub)

    response = _login(client, "dana", "wrong")

    assert (response.status_code, response.json()) == (401, GENERIC)
    assert stub.calls == []


def test_unknown_user_fails_exactly_like_a_wrong_password(
    client: TestClient, app: FastAPI, pool: FakePool
) -> None:
    """'s whole point: no observable difference — the net's 404
    enumeration signal is dead (replaced deliberately at)."""
    pool.users["dana"] = {"sub": "s", "email": None, "password": "right"}
    _use_legacy(app, StubLegacy())  # legacy rejects the unknown one

    unknown = _login(client, "nobody")
    wrong = _login(client, "dana", "wrong")

    assert (unknown.status_code, unknown.json()) == (wrong.status_code, wrong.json())


@pytest.mark.integration
def test_unmigrated_user_fails_generically_and_creates_nothing(
    db_client: TestClient, app: FastAPI, pool: FakePool
) -> None:
    """legacy accepted, but no imported Legacy ID mapping exists."""
    _use_legacy(app, StubLegacy(accepted("999999", "patient")))

    response = _login(db_client, "someuser")

    assert (response.status_code, response.json()) == (401, GENERIC)
    assert pool.users == {}


def test_legacy_outage_fails_generically(client: TestClient, app: FastAPI, pool: FakePool) -> None:
    """A legacy-platform outage collapses to the same generic 401 - nothing created."""
    _use_legacy(app, StubLegacy(unavailable=True))
    response = _login(client, "someuser")
    assert (response.status_code, response.json()) == (401, GENERIC)


def test_migration_disabled_without_a_legacy_client(
    client: TestClient, app: FastAPI, pool: FakePool
) -> None:
    """No STRATA_LEGACY_* configured → unknown users just fail generically."""
    _use_legacy(app, None)
    response = _login(client, "nobody")
    assert (response.status_code, response.json()) == (401, GENERIC)


# --- the gate's hard failures ------------------------------------------


@pytest.mark.integration
def test_already_adopted_profile_fails_hard(
    db_client: TestClient, app: FastAPI, pool: FakePool, db_sessionmaker: Any
) -> None:
    """An active Cognito Sub mapping contradicts the unknown-user signal:
    pool/database drift — never supersede on a guess."""
    run_db(_import_user(db_sessionmaker, profile_id="m-8", legacy_id="2200", email=None))

    async def _adopt_sub() -> None:
        async with db_sessionmaker() as session:
            await adopt(session, user_id="m-8", type_name=COGNITO_SUB, external_id="old-sub")
            await session.commit()

    run_db(_adopt_sub())
    _use_legacy(app, StubLegacy(accepted("2200", "patient")))

    response = _login(db_client, "someuser")

    assert (response.status_code, response.json()) == (401, GENERIC)
    assert pool.users == {}
    assert run_db(_active_sub(db_sessionmaker, "m-8")) == "old-sub"  # untouched


@pytest.mark.integration
def test_email_identifier_must_match_the_imported_profile(
    db_client: TestClient, app: FastAPI, pool: FakePool, db_sessionmaker: Any
) -> None:
    """an email identifier that does not match the imported profile fails hard,
    creating nothing."""
    run_db(
        _import_user(
            db_sessionmaker,
            profile_id="c-8",
            legacy_id="2508",
            email="right@wandahealth.com",
            roles=("Coach",),
        )
    )
    _use_legacy(app, StubLegacy(accepted("2508", "coach")))

    response = _login(db_client, "wrong@wandahealth.com")

    assert (response.status_code, response.json()) == (401, GENERIC)
    assert pool.users == {}


@pytest.mark.integration
def test_kind_discriminator_must_match_the_imported_roles(
    db_client: TestClient, app: FastAPI, pool: FakePool, db_sessionmaker: Any
) -> None:
    """The contract's internal_* discriminator cross-checks the import:
    a 'patient' acceptance must land on a Member profile."""
    run_db(
        _import_user(
            db_sessionmaker, profile_id="c-7", legacy_id="2209", email=None, roles=("Coach",)
        )
    )
    _use_legacy(app, StubLegacy(accepted("2209", "patient")))

    response = _login(db_client, "someuser")

    assert (response.status_code, response.json()) == (401, GENERIC)
    assert pool.users == {}


@pytest.mark.integration
def test_internal_id_mismatch_fails_hard(
    db_client: TestClient, app: FastAPI, pool: FakePool, db_sessionmaker: Any
) -> None:
    """The verdict's internal patient/coach id must equal the
    imported Legacy Summit Patient/Coach ID mapping — a different value is an
    anomaly that fails hard, revealing nothing."""
    run_db(
        _import_user(
            db_sessionmaker, profile_id="i-1", legacy_id="2900", email=None, internal_id="1560"
        )
    )
    _use_legacy(app, StubLegacy(accepted("2900", "patient", internal_id="9999")))

    response = _login(db_client, "someuser")

    assert (response.status_code, response.json()) == (401, GENERIC)
    assert pool.users == {}


@pytest.mark.integration
def test_missing_internal_mapping_fails_hard(
    db_client: TestClient, app: FastAPI, pool: FakePool, db_sessionmaker: Any
) -> None:
    """A pre-split import (Django-user-id row only) cannot migrate until it is
    converged — re-running the import script is the official backfill path."""
    run_db(
        _import_user(
            db_sessionmaker, profile_id="i-2", legacy_id="2901", email=None, with_internal=False
        )
    )
    _use_legacy(app, StubLegacy(accepted("2901", "patient")))

    response = _login(db_client, "someuser")

    assert (response.status_code, response.json()) == (401, GENERIC)
    assert pool.users == {}


# --- compensation and the race ------------------------------


@pytest.mark.integration
def test_mapping_write_failure_compensates_and_the_next_login_retries_cleanly(
    db_client: TestClient,
    app: FastAPI,
    pool: FakePool,
    db_sessionmaker: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AdminCreateUser succeeded but adoption failed → the just-created
    Cognito user is deleted and the SAME login later migrates from clean state."""
    run_db(_import_user(db_sessionmaker, profile_id="m-7", legacy_id="2404", email=None))
    _use_legacy(app, StubLegacy(accepted("2404", "patient")))

    fail_once = {"pending": True}

    async def _flaky_adopt(*args: Any, **kwargs: Any) -> Any:
        if fail_once.pop("pending", False):
            raise RuntimeError("mapping write failed")
        return await adopt(*args, **kwargs)  # the real seam call

    monkeypatch.setattr("strata_engine_auth.services.migration.adopt", _flaky_adopt)
    failed = _login(db_client, "pat7user")
    assert (failed.status_code, failed.json()) == (401, GENERIC)
    assert pool.deleted == ["pat7user"] and "pat7user" not in pool.users

    # The transient fault has cleared; nothing was left behind to block the retry.
    retried = _login(db_client, "pat7user")
    assert retried.status_code == 200
    assert run_db(_active_sub(db_sessionmaker, "m-7")) == "sub-pat7user"


@pytest.mark.integration
def test_concurrent_first_login_absorbs_the_username_race(
    db_client: TestClient, app: FastAPI, pool: FakePool, db_sessionmaker: Any
) -> None:
    """Two first logins race: the loser's AdminCreateUser hits
    UsernameExistsException and its retry signs in against the winner's account."""
    run_db(_import_user(db_sessionmaker, profile_id="m-6", legacy_id="2405", email=None))
    _use_legacy(app, StubLegacy(accepted("2405", "patient")))
    # The winner already completed; our request saw the stale unknown-user signal.
    pool.users["pat6user"] = {"sub": "sub-pat6user", "email": None, "password": PASSWORD}
    pool.hide_next_login = True

    response = _login(db_client, "pat6user")

    assert response.status_code == 200
    assert pool.deleted == []  # absorbed, never compensated


# --- the guided upgrade signal -----------------------------


@pytest.mark.integration
def test_weak_legacy_password_signals_upgrade_and_creates_nothing(
    db_client: TestClient, app: FastAPI, pool: FakePool, db_sessionmaker: Any
) -> None:
    """legacy accepted + gate passed + non-compliant password → the
    distinct 409 challenge, with NOTHING created — abandoning it costs nothing."""
    run_db(_import_user(db_sessionmaker, profile_id="w-1", legacy_id="2600", email=None))
    _use_legacy(app, StubLegacy(accepted("2600", "patient")))

    response = _login(db_client, "weakuser1", WEAK_PASSWORD)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "password_upgrade_required"
    assert pool.users == {} and pool.deleted == []


def test_weak_password_with_rejected_credentials_stays_generic(
    client: TestClient, app: FastAPI, pool: FakePool
) -> None:
    """The signal is EARNED by legacy acceptance: a rejected weak password is
    indistinguishable from any other bad credential (holds)."""
    _use_legacy(app, StubLegacy())  # legacy rejects

    response = _login(client, "nobody", WEAK_PASSWORD)

    assert (response.status_code, response.json()) == (401, GENERIC)


@pytest.mark.integration
def test_weak_password_without_an_imported_mapping_stays_generic(
    db_client: TestClient, app: FastAPI, pool: FakePool
) -> None:
    """The policy check sits AFTER the gate: an unmigrated caller learns nothing
    from their password's strength."""
    _use_legacy(app, StubLegacy(accepted("999998", "patient")))

    response = _login(db_client, "someuser", WEAK_PASSWORD)

    assert (response.status_code, response.json()) == (401, GENERIC)
    assert pool.users == {}


@pytest.mark.integration
def test_weak_password_with_a_kind_mismatch_stays_generic(
    db_client: TestClient, app: FastAPI, pool: FakePool, db_sessionmaker: Any
) -> None:
    """...and after EVERY cross-check: a gate anomaly never turns into the signal."""
    run_db(
        _import_user(
            db_sessionmaker, profile_id="w-2", legacy_id="2601", email=None, roles=("Coach",)
        )
    )
    _use_legacy(app, StubLegacy(accepted("2601", "patient")))

    response = _login(db_client, "someuser", WEAK_PASSWORD)

    assert (response.status_code, response.json()) == (401, GENERIC)
    assert pool.users == {}


@pytest.mark.integration
def test_upgrade_challenge_body_is_static_and_echoes_the_policy(
    db_client: TestClient, app: FastAPI, pool: FakePool, db_sessionmaker: Any
) -> None:
    """One body for every caller who reaches the signal (the 401's discipline):
    structurally different weak passwords produce byte-identical challenges, and
    the payload echoes the CONFIGURED policy, never the caller's unmet subset."""
    run_db(_import_user(db_sessionmaker, profile_id="w-3", legacy_id="2602", email=None))
    run_db(_import_user(db_sessionmaker, profile_id="w-4", legacy_id="2603", email=None))

    _use_legacy(app, StubLegacy(accepted("2602", "patient")))
    first = _login(db_client, "weakuser3", "legacypw")  # no upper/digit/symbol
    _use_legacy(app, StubLegacy(accepted("2603", "patient")))
    second = _login(db_client, "weakuser4", "12345678")  # no upper/lower/symbol

    assert first.status_code == second.status_code == 409
    assert first.content == second.content  # byte-identical, whatever was unmet
    policy = first.json()["detail"]["password_policy"]
    assert policy == {
        "min_length": 8,
        "require_uppercase": True,
        "require_lowercase": True,
        "require_digit": True,
        "require_symbol": True,
    }


@pytest.mark.integration
def test_pool_policy_drift_on_login_compensates_and_signals_upgrade(
    db_client: TestClient, app: FastAPI, pool: FakePool, db_sessionmaker: Any
) -> None:
    """The backstop: a locally compliant password the REAL pool still rejects →
    compensation (no half-created account) and the same 409 remedy."""
    run_db(_import_user(db_sessionmaker, profile_id="w-5", legacy_id="2604", email=None))
    _use_legacy(app, StubLegacy(accepted("2604", "patient")))
    pool.reject_next_password = True

    response = _login(db_client, "weakuser5", PASSWORD)  # passes the local mirror

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "password_upgrade_required"
    assert pool.deleted == ["weakuser5"] and "weakuser5" not in pool.users


@pytest.mark.integration
def test_group_assignment_failure_compensates(
    db_client: TestClient,
    app: FastAPI,
    pool: FakePool,
    db_sessionmaker: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user migrated without their groups would sign in role-less (the
    live finding) — so a failed group assignment compensates like any other
    post-creation failure and the next attempt migrates cleanly."""
    run_db(_import_user(db_sessionmaker, profile_id="m-2b", legacy_id="2412", email=None))
    _use_legacy(app, StubLegacy(accepted("2412", "patient")))

    def _refuse(username: str, group: str) -> None:
        raise _client_error("InternalErrorException")

    monkeypatch.setattr(cognito, "admin_add_user_to_group", _refuse)

    response = _login(db_client, "pat2buser")

    assert (response.status_code, response.json()) == (401, GENERIC)
    assert pool.deleted == ["pat2buser"] and "pat2buser" not in pool.users


@pytest.mark.integration
def test_concurrent_first_logins_of_one_profile_create_one_account(
    app: FastAPI,
    pool: FakePool,
    db_sessionmaker: Any,
) -> None:
    """The same imported user signing in simultaneously by app username AND by
    email (two devices, migration window) must yield exactly ONE Cognito account
    — the profile-row lock serializes the gate so the loser fails hard as an
    already-adopted anomaly rather than creating a second account (review finding)."""
    from strata_engine_auth.services.migration import MigrationLoginFailed, migrate

    run_db(
        _import_user(
            db_sessionmaker,
            profile_id="dup-race",
            legacy_id="2491",
            email="race@example.com",
        )
    )
    # Two verdicts for one profile: the username form and the email form both
    # resolve to django id 2491, so both pass the gate's identity checks.
    by_username = StubLegacy(accepted("2491", "patient"))
    by_email = StubLegacy(accepted("2491", "patient"))

    async def _run() -> list[Any]:
        async def _attempt(legacy: StubLegacy, identifier: str) -> str:
            async with db_sessionmaker() as session:
                await migrate(session, legacy, identifier, PASSWORD)
                return "migrated"

        return list(
            await asyncio.gather(
                _attempt(by_username, "raceuser"),
                _attempt(by_email, "race@example.com"),
                return_exceptions=True,
            )
        )

    results = run_db(_run())

    migrated = [r for r in results if r == "migrated"]
    anomalies = [
        r
        for r in results
        if isinstance(r, MigrationLoginFailed) and str(r) == "already_adopted_anomaly"
    ]
    assert len(migrated) == 1, results
    assert len(anomalies) == 1, results
    live = [u for u in pool.users if u not in pool.deleted]
    assert len(live) == 1, f"expected exactly one live pool account, got {live}"


@pytest.mark.integration
def test_cancellation_mid_flow_still_compensates(
    app: FastAPI,
    pool: FakePool,
    db_sessionmaker: Any,
) -> None:
    """under a client disconnect: a CancelledError (a BaseException, not an
    Exception) raised after AdminCreateUser must still delete the orphaned account
    and re-raise — never leave a half-created Cognito user behind (review finding)."""
    from strata_engine_auth.services.migration import migrate

    run_db(_import_user(db_sessionmaker, profile_id="m-cancel", legacy_id="2490", email=None))
    pool.cancel_next_password = True
    legacy = StubLegacy(accepted("2490", "patient"))

    async def _run() -> None:
        async with db_sessionmaker() as session:
            await migrate(session, legacy, "patcanceluser", PASSWORD)

    with pytest.raises(asyncio.CancelledError):
        run_db(_run())

    assert pool.deleted == ["patcanceluser"] and "patcanceluser" not in pool.users


# --- the admin channel (AWS credential chain) failing (live finding) --------------


@pytest.mark.integration
def test_admin_channel_failure_fails_generically(
    db_client: TestClient,
    app: FastAPI,
    pool: FakePool,
    db_sessionmaker: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A credential-chain failure (expired AWS session, no IAM role) raises
    BotoCoreError, not ClientError — found live at the walkthrough as a 500.
    It must collapse to the same generic 401 as every other migration failure."""
    run_db(_import_user(db_sessionmaker, profile_id="m-4b", legacy_id="2410", email=None))
    _use_legacy(app, StubLegacy(accepted("2410", "patient")))

    def _chain_down(username: str, email: str | None) -> str:
        raise EndpointConnectionError(endpoint_url="https://cognito-idp.test")

    monkeypatch.setattr(cognito, "admin_create_user", _chain_down)

    response = _login(db_client, "pat4buser")

    assert (response.status_code, response.json()) == (401, GENERIC)
    assert pool.users == {}


@pytest.mark.integration
def test_admin_channel_dying_mid_flow_absorbs_the_failed_compensation(
    db_client: TestClient,
    app: FastAPI,
    pool: FakePool,
    db_sessionmaker: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Credentials die between create and set-password: the compensating delete
    fails too. Still the generic 401 — the stuck account is logged for ops
    (migration_compensation_failed), never a 500."""
    run_db(_import_user(db_sessionmaker, profile_id="m-3b", legacy_id="2411", email=None))
    _use_legacy(app, StubLegacy(accepted("2411", "patient")))

    def _chain_down(*args: Any) -> None:
        raise EndpointConnectionError(endpoint_url="https://cognito-idp.test")

    monkeypatch.setattr(cognito, "admin_set_permanent_password", _chain_down)
    monkeypatch.setattr(cognito, "admin_delete_user", _chain_down)

    response = _login(db_client, "pat3buser")

    assert (response.status_code, response.json()) == (401, GENERIC)
    assert "pat3buser" in pool.users  # left behind (channel down) — logged, not 500


# --- log safety --------------------------------------------------------------


def test_unhandled_exceptions_never_print_request_locals(
    app: FastAPI,
    pool: FakePool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """'s harshest case, found live at the walkthrough: an exception NO
    handler catches still must not leak credentials — the login/migrate frames
    hold the raw password in their locals, so traceback rendering must never
    print locals."""

    def _boom(identifier: str, password: str) -> Any:
        raise RuntimeError("simulated unhandled failure")

    monkeypatch.setattr(cognito, "login", _boom)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/v1/auth/login", json={"identifier": "leaky-user-id", "password": "Leaky-Pw!500"}
        )

    assert response.status_code == 500
    captured = capsys.readouterr()
    for secret in ("Leaky-Pw!500", "leaky-user-id"):
        assert secret not in captured.out and secret not in captured.err


@pytest.mark.integration
def test_credentials_and_identifiers_never_reach_the_logs(
    db_client: TestClient,
    app: FastAPI,
    pool: FakePool,
    db_sessionmaker: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """across a success, a rejection, and a compensated failure: the
    password and the typed identifier appear in no log line (leg names and
    correlation ids only)."""
    run_db(_import_user(db_sessionmaker, profile_id="m-5b", legacy_id="2406", email=None))
    _use_legacy(app, StubLegacy(accepted("2406", "patient")))

    assert _login(db_client, "pat5buser").status_code == 200  # migrates
    _use_legacy(app, StubLegacy())  # now rejecting
    assert _login(db_client, "unknownperson", "Wrong-Pw!x").status_code == 401

    captured = capsys.readouterr()
    for secret in (PASSWORD, "Wrong-Pw!x", "pat5buser", "unknownperson"):
        assert secret not in captured.out and secret not in captured.err
