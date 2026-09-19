"""The guided password upgrade endpoint (; /i).

Same harness as the migration suite: Cognito is the ``FakePool``, the legacy
platform a recording stub, DB-touching scenarios on the ephemeral Postgres.
The endpoint is stateless — every submission re-earns migration (probe →
legacy re-proof → the full gate) and completes it with the compliant NEW
password; everything that is not exactly that collapses to the generic 401.
"""

from typing import Any

import pytest
from conftest import run_db
from fastapi import FastAPI
from fastapi.testclient import TestClient
from strata_identity.identifiers import adopt
from test_migration_login import (
    GENERIC,
    NEW_PASSWORD,
    PASSWORD,
    WEAK_PASSWORD,
    FakePool,
    StubLegacy,
    _active_sub,
    _import_user,
    _use_legacy,
    accepted,
)

from strata_engine_auth.services import cognito


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


def _upgrade(
    client: TestClient, identifier: str, password: str = WEAK_PASSWORD, new: str = NEW_PASSWORD
) -> Any:
    return client.post(
        "/v1/auth/password-upgrade",
        json={"identifier": identifier, "password": password, "new_password": new},
    )


# --- the happy journeys --------------------------------------------------


@pytest.mark.integration
def test_password_upgrade_migrates_with_the_new_password(
    db_client: TestClient, app: FastAPI, pool: FakePool, db_sessionmaker: Any
) -> None:
    """The full journey: the weak-password login signals, the upgrade
    submission migrates with the NEW password, and the next login is native."""
    run_db(_import_user(db_sessionmaker, profile_id="u-1", legacy_id="2700", email=None))
    stub = StubLegacy(accepted("2700", "patient"))
    _use_legacy(app, stub)

    signal = db_client.post(
        "/v1/auth/login", json={"identifier": "upuser1", "password": WEAK_PASSWORD}
    )
    assert signal.status_code == 409

    response = _upgrade(db_client, "upuser1")

    assert response.status_code == 200 and "access_token" in response.json()
    assert pool.users["upuser1"]["password"] == NEW_PASSWORD  # never the weak one
    assert pool.users["upuser1"]["groups"] == {"member"}  # roles ride as groups
    assert stub.calls == ["upuser1", "upuser1"]  # the old password re-proven
    assert run_db(_active_sub(db_sessionmaker, "u-1")) == "sub-upuser1"

    native = db_client.post(
        "/v1/auth/login", json={"identifier": "upuser1", "password": NEW_PASSWORD}
    )
    assert native.status_code == 200  # with the upgraded password


@pytest.mark.integration
def test_password_upgrade_by_email_gets_a_synthetic_username(
    db_client: TestClient, app: FastAPI, pool: FakePool, db_sessionmaker: Any
) -> None:
    """Parity with the login path: an email identifier still lands on the
    profile-id username with the verified email alias serving sign-ins."""
    run_db(
        _import_user(
            db_sessionmaker,
            profile_id="u-2",
            legacy_id="2701",
            email="upgrade.coach@wandahealth.com",
            roles=("Coach",),
        )
    )
    _use_legacy(app, StubLegacy(accepted("2701", "coach")))

    response = _upgrade(db_client, "upgrade.coach@wandahealth.com")

    assert response.status_code == 200
    assert "upgrade.coach@wandahealth.com" not in pool.users
    assert pool.users["u-2"]["password"] == NEW_PASSWORD
    assert run_db(_active_sub(db_sessionmaker, "u-2")) == "sub-u-2"


# --- rejected before anything is touched (validation half) ----------------


def test_weak_new_password_is_rejected_before_any_credential_check(
    client: TestClient, app: FastAPI, pool: FakePool
) -> None:
    """A non-compliant new password gets the SAME static 409 challenge as the
    login signal (it depends only on the request body — no enumeration surface)
    and NO credential leaves the process."""
    stub = StubLegacy(accepted("2702", "patient"))
    _use_legacy(app, stub)

    response = _upgrade(client, "upuser3", new="stillweak")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "password_upgrade_required"
    assert stub.calls == [] and pool.users == {}


# --- the generic collapse ------------------------------------


def test_wrong_old_password_fails_generically(
    client: TestClient, app: FastAPI, pool: FakePool
) -> None:
    """a wrong legacy password on the upgrade fails with the generic 401; nothing is
    created."""
    _use_legacy(app, StubLegacy())  # legacy rejects the old password

    response = _upgrade(client, "upuser4")

    assert (response.status_code, response.json()) == (401, GENERIC)
    assert pool.users == {}


@pytest.mark.integration
def test_upgrade_reruns_the_full_gate(db_client: TestClient, app: FastAPI, pool: FakePool) -> None:
    """Stateless means stateless: no imported mapping → the second request fails
    exactly like the first would have, creating nothing."""
    _use_legacy(app, StubLegacy(accepted("999997", "patient")))

    response = _upgrade(db_client, "upuser5")

    assert (response.status_code, response.json()) == (401, GENERIC)
    assert pool.users == {}


def test_stale_screen_after_a_completed_upgrade_fails_generically(
    client: TestClient, app: FastAPI, pool: FakePool
) -> None:
    """Tab B submits after tab A already upgraded: the probe meets a known user
    with a now-wrong password and stops right there (— legacy untouched)."""
    pool.users["upuser6"] = {"sub": "s6", "email": None, "password": NEW_PASSWORD}
    stub = StubLegacy(accepted("2703", "patient"))
    _use_legacy(app, stub)

    response = _upgrade(client, "upuser6")  # still carrying the old weak password

    assert (response.status_code, response.json()) == (401, GENERIC)
    assert stub.calls == [] and pool.deleted == []


def test_valid_native_old_password_fails_generically(
    client: TestClient, app: FastAPI, pool: FakePool
) -> None:
    """An account that signs in natively has nothing to migrate: the tokens are
    discarded and NO password changes — this endpoint is not a general
    change-password surface ('s explicit exclusion)."""
    pool.users["upuser7"] = {"sub": "s7", "email": None, "password": PASSWORD}
    stub = StubLegacy(accepted("2704", "patient"))
    _use_legacy(app, stub)

    response = _upgrade(client, "upuser7", password=PASSWORD)

    assert (response.status_code, response.json()) == (401, GENERIC)
    assert pool.users["upuser7"]["password"] == PASSWORD  # untouched
    assert stub.calls == []


def test_upgrade_disabled_without_a_legacy_client(
    client: TestClient, app: FastAPI, pool: FakePool
) -> None:
    """Without a legacy client the upgrade endpoint fails generically - no migration surface
    exists."""
    _use_legacy(app, None)
    response = _upgrade(client, "upuser8")
    assert (response.status_code, response.json()) == (401, GENERIC)


# --- races, compensation, drift -------------------------


@pytest.mark.integration
def test_double_submit_race_is_absorbed_and_converges(
    db_client: TestClient, app: FastAPI, pool: FakePool, db_sessionmaker: Any
) -> None:
    """The loser of a concurrent double-submit hits UsernameExistsException,
    absorbs it, and its retry — with the NEW password — signs in against the
    winner's account (which set the same new password)."""
    run_db(_import_user(db_sessionmaker, profile_id="u-9", legacy_id="2705", email=None))
    _use_legacy(app, StubLegacy(accepted("2705", "patient")))
    # The winner created the account already; our probe saw the stale signal.
    pool.users["upuser9"] = {"sub": "sub-upuser9", "email": None, "password": NEW_PASSWORD}
    pool.hide_next_login = True

    response = _upgrade(db_client, "upuser9")

    assert response.status_code == 200
    assert pool.deleted == []  # absorbed, never compensated


@pytest.mark.integration
def test_upgrade_adoption_failure_compensates(
    db_client: TestClient,
    app: FastAPI,
    pool: FakePool,
    db_sessionmaker: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """parity: adoption fails after the account was created with the new
    password → compensating delete, and the SAME submission later succeeds."""
    run_db(_import_user(db_sessionmaker, profile_id="u-10", legacy_id="2706", email=None))
    _use_legacy(app, StubLegacy(accepted("2706", "patient")))

    fail_once = {"pending": True}

    async def _flaky_adopt(*args: Any, **kwargs: Any) -> Any:
        if fail_once.pop("pending", False):
            raise RuntimeError("mapping write failed")
        return await adopt(*args, **kwargs)  # the real seam call

    monkeypatch.setattr("strata_engine_auth.services.migration.adopt", _flaky_adopt)
    failed = _upgrade(db_client, "upuser10")
    assert (failed.status_code, failed.json()) == (401, GENERIC)
    assert pool.deleted == ["upuser10"] and "upuser10" not in pool.users

    retried = _upgrade(db_client, "upuser10")
    assert retried.status_code == 200
    assert run_db(_active_sub(db_sessionmaker, "u-10")) == "sub-upuser10"


@pytest.mark.integration
def test_pool_policy_drift_on_upgrade_returns_400_and_compensates(
    db_client: TestClient, app: FastAPI, pool: FakePool, db_sessionmaker: Any
) -> None:
    """When the REAL pool rejects a locally compliant new password, a 409 would
    loop the caller forever (they just satisfied the advertised policy) — the
    endpoint compensates and fails as a plain 400 instead."""
    run_db(_import_user(db_sessionmaker, profile_id="u-11", legacy_id="2707", email=None))
    _use_legacy(app, StubLegacy(accepted("2707", "patient")))
    pool.reject_next_password = True

    response = _upgrade(db_client, "upuser11")

    assert (response.status_code, response.json()) == (400, {"detail": "Invalid request"})
    assert pool.deleted == ["upuser11"] and "upuser11" not in pool.users


# --- log safety (extended to the new credential) -------------------------------


@pytest.mark.integration
def test_upgrade_credentials_and_unmet_requirements_never_reach_the_logs(
    db_client: TestClient,
    app: FastAPI,
    pool: FakePool,
    db_sessionmaker: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Across the login signal, a rejected new password, and a completed
    upgrade: neither password, no identifier, and no unmet-requirement label
    reaches a log line — the new password is a live credential the moment the
    upgrade lands."""
    run_db(_import_user(db_sessionmaker, profile_id="u-12", legacy_id="2708", email=None))
    _use_legacy(app, StubLegacy(accepted("2708", "patient")))

    signal = db_client.post(
        "/v1/auth/login", json={"identifier": "upuser12", "password": WEAK_PASSWORD}
    )
    assert signal.status_code == 409
    assert _upgrade(db_client, "upuser12", new="alsoweak").status_code == 409
    assert _upgrade(db_client, "upuser12").status_code == 200

    captured = capsys.readouterr()
    # "uppercase" pins the unmet-requirement labels out of the logs too — the
    # challenge body echoes only the configured policy, never a per-user subset.
    for secret in (WEAK_PASSWORD, NEW_PASSWORD, "alsoweak", "upuser12", "uppercase"):
        assert secret not in captured.out and secret not in captured.err
