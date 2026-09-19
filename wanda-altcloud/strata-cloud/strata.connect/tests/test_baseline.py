"""Service-baseline conformance gate — CI-enforced.

Fails the build if the service drifts from the platform baseline: the observability correlation-ID
middleware must be installed, APIs must be versioned under /v1, and — this service being
classified **clinical** — no clinical value may ever reach a log line.
"""

import logging

import pytest
from fastapi.testclient import TestClient

from app.api.middleware import CorrelationIdMiddleware
from app.main import create_app
from tests.conftest import EDGE_SECRET


def test_observability_middleware_is_installed() -> None:
    """The baseline correlation-ID middleware is installed on the app."""
    app = create_app()
    assert any(m.cls is CorrelationIdMiddleware for m in app.user_middleware), (
        "service baseline violated: CorrelationIdMiddleware is not installed "
    )


def test_apis_are_versioned(client: TestClient) -> None:
    # 401 (route exists, auth gate first) under /v1; 404 (no route) unversioned.
    """Business routes live under /v1 (auth-gated); unversioned paths do not exist."""
    assert client.post("/v1/readings", json={}).status_code == 401
    assert client.post("/readings", json={}).status_code == 404


@pytest.mark.anyio
async def test_db_errors_hide_bound_parameters() -> None:
    """Companion to the log-capture gate: a raising DB path must not carry bound
    parameters (clinical values) in the exception text — that text reaches logs and the
    queue's last_error column via the consumer's failure handling. Guards the engine's
    hide_parameters=True; a red run here means a leak channel reopened. Do not weaken."""
    from datetime import UTC, datetime

    from sqlalchemy.exc import IntegrityError
    from strata_core.domains.device_readings import RawReading

    from app.db import session_factory

    sentinel_payload = {"weight_kg": "SENTINEL-DB-ERROR-VALUE"}
    received = datetime(2026, 7, 1, tzinfo=UTC)
    async with session_factory()() as session:
        session.add(
            RawReading(
                payload=sentinel_payload, correlation_id="baseline-dup", received_at=received
            )
        )
        await session.commit()

    with pytest.raises(IntegrityError) as excinfo:
        async with session_factory()() as session:
            session.add(  # the unique correlation_id makes this INSERT fail deterministically
                RawReading(
                    payload=sentinel_payload, correlation_id="baseline-dup", received_at=received
                )
            )
            await session.flush()

    rendered = str(excinfo.value) + repr(excinfo.value)
    assert "SENTINEL-DB-ERROR-VALUE" not in rendered, (
        "bound parameters leaked into a DB exception — /violation"
    )


def test_suite_pins_dev_mode_off() -> None:
    """The suite pins STRATA_DEV_MODE=false — a developer .env cannot leak dev routes
    into tests, and the absent-by-default guard holds in any order."""
    import os

    from app.config import settings

    assert os.environ["STRATA_DEV_MODE"] == "false"
    assert settings.dev_mode is False


def test_dev_mode_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Baseline fail-closed doctrine: STRATA_DEV_MODE=true must refuse startup unless
    STRATA_ENVIRONMENT is EXPLICITLY 'local' — the Settings default never counts.
    (_env_file=None keeps a developer's local .env out of the assertion.)"""
    from pydantic import ValidationError

    from app.config import Settings

    monkeypatch.delenv("STRATA_ENVIRONMENT", raising=False)
    with pytest.raises(ValidationError):
        Settings(dev_mode=True, _env_file=None)

    monkeypatch.setenv("STRATA_ENVIRONMENT", "local")
    assert Settings(dev_mode=True, _env_file=None).dev_mode is True


# Values a real reading could carry, made unmistakable. Identifiers (reading_id, device_id,
# correlation ids) are deliberately NOT sentinels — identifiers in logs are allowed.
_CLINICAL_SENTINELS = ("987654.321", "SENTINEL-CLINICAL-VALUE", "424242.99")


@pytest.mark.anyio
async def test_no_clinical_values_in_logs(
    client: TestClient,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The log-safety gate for a `clinical` service. Do not weaken.

    Drives the clinical paths — ingest, then the pipeline over both a valid-but-unregistered
    reading (the quarantine path) and a malformed one (the validation-failure path) — with
    sentinel values, while capturing every logging channel: structlog (stdout) and stdlib
    records at DEBUG, which is where SQLAlchemy's statement echo would surface. No sentinel
    may appear anywhere. This is what makes "no clinical values in logs" unable to regress
    silently: re-adding a `db_echo` switch (the finding), logging a payload, or a
    validation error echoing input values turns this test red in CI. Extend the drive when
    a story adds a new path that touches clinical values.
    """
    from sqlalchemy import select
    from strata_core.domains.device_readings import WeightReading
    from strata_core.fixtures import create_profile

    from app.db import session_factory
    from app.services.consumer import drain
    from app.services.registration import register_device

    # A registered sentinel device so the drive covers the PERSISTENCE path too.
    async with session_factory()() as session:
        profile = await create_profile(
            session,
            email="baseline-sentinel@wanda.test",
            first_name="Baseline",
            last_name="Sentinel",
            cognito_sub="baseline-sentinel-sub",
        )
        await register_device(
            session,
            user_id=profile.id,
            identifier_type="SmartMeter Scale",
            external_id="SM-BASELINE-REGISTERED",
        )
        await session.commit()

    valid_unregistered = {
        "reading_id": 24681357,
        "device_id": "SM-BASELINE-SENTINEL",
        "device_model": "SM-BASELINE",
        "reading_type": "weight",
        "date_recorded": "2026-07-01T08:15:00",
        "date_received": "2026-07-01T08:16:05",
        "weight_kg": 987654.321,
        "weight_lbs": 424242.99,
        "clinical_note": "SENTINEL-CLINICAL-VALUE",
    }
    malformed = {
        "reading_id": 24681358,
        "device_id": "SM-BASELINE-SENTINEL",
        "reading_type": "blood_pressure",
        "systolic_mmhg": "SENTINEL-CLINICAL-VALUE",  # wrong type → validation-failure path
    }
    valid_registered = {  # exercises the full persistence path (suspect values, stored)
        **valid_unregistered,
        "reading_id": 24681359,
        "device_id": "SM-BASELINE-REGISTERED",
    }

    with caplog.at_level(logging.DEBUG):
        for payload in (valid_unregistered, malformed, valid_registered):
            response = client.post("/v1/readings", json=payload, headers={"X-API-Key": EDGE_SECRET})
            assert response.status_code == 202
        await drain()  # quarantine + validation-failure processing, logs and all

    captured = capsys.readouterr()
    everything = captured.out + captured.err + "\n".join(r.getMessage() for r in caplog.records)
    # Self-checks: the capture must actually be capturing all three exercised paths.
    assert "reading captured" in everything, "log capture saw nothing; the gate is not testing"
    assert "reading quarantined" in everything
    assert "failed validation" in everything
    assert "reading persisted" in everything  # the persistence path really ran

    async with session_factory()() as session:
        stored = (
            await session.execute(
                select(WeightReading).where(WeightReading.provider_reading_id == "24681359")
            )
        ).scalar_one()
        assert stored.suspect is True  # flagged, stored, and never logged

    for sentinel in _CLINICAL_SENTINELS:
        assert sentinel not in everything, (
            f"clinical value {sentinel!r} reached a log line — /violation"
        )


@pytest.mark.anyio
async def test_no_clinical_values_on_operator_surfaces(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The log-safety gate, extended: the operator listings and their dev-mode HTTP
    mirrors are egress surfaces too — `last_error` moved from DB-only to printed and
    served the day the tooling landed. Drive sentinel readings to quarantine and to
    undeliverable, then assert no sentinel appears in the listing summaries (what the
    CLI prints) or the dev endpoints' JSON. A future field added to a summary or a
    value-bearing error recorded into last_error turns this red in CI. Do not weaken.
    """
    from app.config import settings
    from app.db import session_factory
    from app.services import operations
    from app.services.consumer import drain

    quarantine_bound = {  # valid, sentinel values, device never registered
        "reading_id": 24681360,
        "device_id": "SM-BASELINE-SENTINEL-OPS",
        "device_model": "SM-BASELINE",
        "reading_type": "weight",
        "date_recorded": "2026-07-01T08:15:00",
        "date_received": "2026-07-01T08:16:05",
        "weight_kg": 987654.321,
        "weight_lbs": 424242.99,
        "clinical_note": "SENTINEL-CLINICAL-VALUE",
    }
    malformed = {  # wrong type → validation failure → retry ladder → undeliverable
        "reading_id": 24681361,
        "device_id": "SM-BASELINE-SENTINEL-OPS",
        "reading_type": "blood_pressure",
        "systolic_mmhg": "SENTINEL-CLINICAL-VALUE",
    }
    for payload in (quarantine_bound, malformed):
        response = client.post("/v1/readings", json=payload, headers={"X-API-Key": EDGE_SECRET})
        assert response.status_code == 202
    await drain()  # retry delay is 0 suite-wide: the malformed one dead-letters in-run

    async with session_factory()() as session:
        queue_summaries = await operations.undeliverable_entries(session) + (
            await operations.in_flight_entries(session)
        )
        quarantine_summaries = await operations.quarantined_readings(session)
    rendered = repr(queue_summaries) + repr(quarantine_summaries)
    # Self-checks: the drive populated exactly what this gate asserts over.
    assert any("systolic_mmhg" in (e.last_error or "") for e in queue_summaries), (
        "no value-free error shape in the listing; the gate is not testing last_error"
    )
    assert any(q.device_id == "SM-BASELINE-SENTINEL-OPS" for q in quarantine_summaries)

    # The dev-mode HTTP mirrors: unauthenticated JSON egress of the same data.
    monkeypatch.setattr(settings, "dev_mode", True)
    dev_client = TestClient(create_app())
    undeliverable_json = dev_client.get("/v1/dev/queue/undeliverable")
    quarantine_json = dev_client.get("/v1/dev/quarantine")
    assert undeliverable_json.status_code == 200 and quarantine_json.status_code == 200
    rendered += undeliverable_json.text + quarantine_json.text

    for sentinel in _CLINICAL_SENTINELS:
        assert sentinel not in rendered, (
            f"clinical value {sentinel!r} reached an operator surface — /violation"
        )


def test_unhandled_exceptions_never_print_request_locals(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An exception NO handler catches must not render frame locals — request
    handlers hold member data in theirs (strata.engine.auth's
    pin, where the leak was proven live at the Identity walkthrough)."""
    app = create_app()
    sentinel = "PII-sentinel-XK552291"

    @app.post("/pws190-boom")
    async def _boom(payload: dict[str, str]) -> None:
        raise RuntimeError(f"simulated unhandled failure ({len(payload)} fields held)")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/pws190-boom", json={"member_reading": sentinel})

    assert response.status_code == 500
    captured = capsys.readouterr()
    assert sentinel not in captured.out and sentinel not in captured.err, (
        "the traceback rendered request locals - a PII leak "
    )
