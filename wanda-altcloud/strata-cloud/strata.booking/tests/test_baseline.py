"""Service-baseline conformance gate — CI-enforced.

Fails the build if the service drifts from the platform baseline: the observability correlation-ID
middleware must be installed, APIs must be versioned under /v1, and — since the service's
classification rose to `clinical` (: programme membership implies a health
condition — inference counts) — the log-safety gates hold: bound parameters hidden, and no
condition-implying value in any logging channel.
"""

import asyncio
import logging
from datetime import UTC, date, datetime, time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from strata_booking.api.middleware import CorrelationIdMiddleware
from strata_booking.main import create_app


def test_observability_middleware_is_installed() -> None:
    """The baseline correlation-ID middleware is installed on the app."""
    app = create_app()
    assert any(m.cls is CorrelationIdMiddleware for m in app.user_middleware), (
        "service baseline violated: CorrelationIdMiddleware is not installed "
    )


def test_apis_are_versioned(client: TestClient) -> None:
    # The versioned route exists (401 = reached auth, not routing); the unversioned path 404s.
    """Business routes live under /v1 (auth-gated); unversioned paths do not exist."""
    assert client.get("/v1/availability-patterns").status_code == 401
    assert client.get("/availability-patterns").status_code == 404


def test_production_engine_hides_bound_parameters() -> None:
    """The engine the app ships with must carry ``hide_parameters=True`` (
    `clinical` service). The conftest engine mirrors it so the gates below
    exercise the same posture; this pins the production path itself."""
    from strata_booking.db.session import init_engine

    engine = init_engine()
    try:
        assert engine.sync_engine.hide_parameters is True, (
            "clinical baseline violated: init_engine lost hide_parameters "
        )
    finally:
        asyncio.run(engine.dispose())


@pytest.mark.anyio
async def test_db_errors_hide_bound_parameters(session) -> None:  # type: ignore[no-untyped-def]
    """A raising DB path must not carry bound parameters in the exception text — that text
    reaches logs. Guards ``hide_parameters=True`` (the strata.connect exemplar). Do not weaken."""
    from strata_core.domains.kernel import Programme

    sentinel = "SENTINEL-CONDITION-PROGRAMME-NAME"
    session.add(Programme(id="p-baseline-dup", name=sentinel))
    await session.flush()
    with pytest.raises(IntegrityError) as excinfo:
        session.add(Programme(id="p-baseline-dup", name=sentinel))  # duplicate PK
        await session.flush()
    rendered = str(excinfo.value) + repr(excinfo.value)
    assert sentinel not in rendered, "bound parameters leaked into a DB exception — violation"


# Values that would imply a member's condition, made unmistakable. Identifiers (member ids,
# programme ids, appointment ids) are deliberately NOT sentinels — identifiers-only logging
# allows them; it is names and member-supplied text that must never appear.
_CONDITION_SENTINELS = (
    "SENTINEL-CONDITION-PROGRAMME",
    "SENTINEL-MEMBER-NAME",
    "SENTINEL-HEALTH-REASON",
)


@pytest.mark.anyio
async def test_no_condition_implying_values_in_logs(
    _database: AsyncEngine,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The log-safety gate for a `clinical` service. Do not weaken.

    Drives the paths that touch condition-implying values — programme + people setup, slot
    generation, booking, and a member cancellation carrying free text — with sentinel values,
    while capturing every logging channel: structlog (stdout/stderr) and stdlib records at
    DEBUG, where SQLAlchemy's statement logging surfaces. No sentinel may appear anywhere:
    re-enabling parameter rendering, logging a programme name, or echoing member-supplied
    text turns this red in CI. Extend the drive when a story adds a new path that touches
    condition-implying values.
    """
    from strata_core.domains.booking import Appointment
    from strata_core.domains.kernel import Programme

    from strata_booking.core import clock as clock_module
    from strata_booking.schemas.availability import PatternBody
    from strata_booking.services.authz import ActingContext
    from strata_booking.services.availability import create_pattern
    from strata_booking.services.booking import book, browse_slots
    from strata_booking.services.cancellation import cancel_appointment
    from tests.auth import as_principal
    from tests.identity_stubs import Coach, Member, ProgrammeAssignment

    monkeypatch.setattr(
        clock_module.clock.__class__, "now", lambda self: datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
    )
    programme_id, coach_id, member_id = "p-base-sent", "c-base-sent", "m-base-sent"

    with caplog.at_level(logging.DEBUG):
        # SQLAlchemy pins its root logger to WARN at import; raising sqlalchemy.engine
        # to INFO turns statement logging ON for connections opened below — the echo
        # channel where parameter rendering would leak if hide_parameters were removed.
        # Deliberately INFO, not DEBUG: DEBUG additionally logs RESULT ROWS, which no
        # flag can redact and no booking config path can enable (db_echo was removed)
        # — the gate pins the channels a configuration flip could reach.
        caplog.set_level(logging.INFO, logger="sqlalchemy.engine")
        # This gate manages its own connection instead of the shared `session` fixture:
        # SQLAlchemy snapshots the statement-logging decision when a connection is
        # CREATED, so the connection must open after the level bump above — the fixture's
        # opens at setup, before it. Isolation is preserved by hand: same outer
        # transaction + savepoint pattern, rolled back below.
        async with _database.connect() as connection:
            outer = await connection.begin()
            session = AsyncSession(
                bind=connection,
                join_transaction_mode="create_savepoint",
                expire_on_commit=False,
            )
            try:
                session.add(Programme(id=programme_id, name="SENTINEL-CONDITION-PROGRAMME"))
                session.add(Coach(id=coach_id, display_name="Baseline Coach", timezone="UTC"))
                session.add(
                    Member(id=member_id, display_name="SENTINEL-MEMBER-NAME", timezone="UTC")
                )
                for kind, uid in (("coach", coach_id), ("member", member_id)):
                    session.add(
                        ProgrammeAssignment(
                            id=f"pa-base-sent-{kind}",
                            programme_id=programme_id,
                            user_kind=kind,
                            user_id=uid,
                        )
                    )
                await session.flush()
                await create_pattern(
                    session,
                    ActingContext(
                        coach_id=coach_id, acting_user_id=coach_id, on_behalf_of_coach_id=None
                    ),
                    PatternBody(
                        days_of_week=[0],
                        start_time_local=time(9, 0),
                        end_time_local=time(10, 0),
                        slot_duration_minutes=20,
                        timezone="UTC",
                        active_from=date(2026, 10, 1),
                        active_to=date(2026, 10, 31),
                    ),
                )
                slots = await browse_slots(
                    session,
                    member_id,
                    language_match=False,
                    from_date=date(2026, 10, 1),
                    to_date=date(2026, 10, 31),
                )
                assert slots, "sentinel drive found no slots — the gate is not testing"
                appointment_out, replayed = await book(
                    session,
                    as_principal(member_id, "member"),
                    slot_id=slots[0].slot_id,
                    member_id=member_id,
                    language_matched=False,
                    created_by="member",
                    on_behalf_of_coach_id=None,
                )
                assert replayed is False
                appointment = await session.get(Appointment, appointment_out.id)
                assert appointment is not None
                await cancel_appointment(
                    session,
                    as_principal(member_id, "member"),
                    appointment,
                    reason="SENTINEL-HEALTH-REASON",
                    on_behalf_of_coach_id=None,
                )
            finally:
                await session.close()
                if outer.is_active:
                    await outer.rollback()

    captured = capsys.readouterr()
    everything = captured.out + captured.err + "\n".join(r.getMessage() for r in caplog.records)
    # Self-check: the capture really saw the SQL channel (statement text is allowed;
    # parameters are what hide_parameters suppresses).
    assert "INSERT INTO appointments" in everything, (
        "log capture saw no SQL — the gate is not testing the statement-logging channel"
    )
    for sentinel in _CONDITION_SENTINELS:
        assert sentinel not in everything, f"{sentinel} leaked into a logging channel — violation "
