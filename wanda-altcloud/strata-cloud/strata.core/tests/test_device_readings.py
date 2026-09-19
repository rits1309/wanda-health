"""Device readings domain round-trips against a migrated ephemeral Postgres.

Proves the domain migration gives the models the constraints the readings domain
demands: the deterministic type catalogue, the one-active-mapping-per-device
partial unique with supersede semantics, both dedup
keys, and unit systems persisted exactly as supplied.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from strata_core.domains.device_readings import (
    READING_TYPE_EXTERNAL_ID_TYPES,
    READING_TYPES,
    BloodPressureReading,
    QuarantinedReading,
    RawReading,
    ReadingQueueEntry,
    WeightReading,
)
from strata_core.domains.kernel import (
    EXTERNAL_ID_TYPE_IDS,
    EXTERNAL_ID_TYPE_NAMES,
    UserExternalId,
    UserExternalIdType,
    UserProfile,
)
from strata_core.fixtures import create_profile

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SCALE_TYPE_ID = EXTERNAL_ID_TYPE_IDS["SmartMeter Scale"]
BP_TYPE_ID = EXTERNAL_ID_TYPE_IDS["SmartMeter Blood Pressure"]

T0 = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
T1 = datetime(2026, 7, 13, 13, 0, tzinfo=UTC)


async def test_reading_type_map_spans_both_vocabularies() -> None:
    """The resolution map covers every reading type, and every device
    identifier type it names exists in the catalogue — a SUBSET since Identity,
    which added the non-device identity types (`Cognito Sub`, the `Legacy Summit` family)."""
    assert set(READING_TYPE_EXTERNAL_ID_TYPES) == set(READING_TYPES)
    assert set(READING_TYPE_EXTERNAL_ID_TYPES.values()) <= set(EXTERNAL_ID_TYPE_NAMES)
    assert "Cognito Sub" not in READING_TYPE_EXTERNAL_ID_TYPES.values()  # devices only


async def _profile(session: AsyncSession, sub: str = "sub-readings-1") -> UserProfile:
    profile = await create_profile(
        session,
        email=f"{sub}@example.test",
        first_name="Sarah",
        last_name="Mitchell",
        cognito_sub=sub,
    )
    await session.commit()
    return profile


async def _raw(session: AsyncSession, correlation_id: str) -> RawReading:
    raw = RawReading(payload={"reading_id": 12345}, correlation_id=correlation_id, received_at=T0)
    session.add(raw)
    await session.commit()
    return raw


def _weight_reading(
    user_id: str, raw_ref: str, correlation_id: str, provider_reading_id: str = "12346"
) -> WeightReading:
    """The worked weight example: both unit systems, as supplied."""
    return WeightReading(
        user_id=user_id,
        device_id="SM5000-IB-0001",
        provider_reading_id=provider_reading_id,
        recorded_at=T0,
        received_at=T0,
        weight_kg=Decimal("81.6"),
        tare_kg=Decimal("0.0"),
        weight_lbs=Decimal("180.0"),
        tare_lbs=Decimal("0.0"),
        correlation_id=correlation_id,
        raw_ref=raw_ref,
    )


def _bp_reading(
    user_id: str, raw_ref: str, correlation_id: str, provider_reading_id: str = "12345"
) -> BloodPressureReading:
    """The worked blood pressure example."""
    return BloodPressureReading(
        user_id=user_id,
        device_id="SM5000-IB-0002",
        provider_reading_id=provider_reading_id,
        recorded_at=T0,
        received_at=T0,
        systolic_mmhg=128,
        diastolic_mmhg=82,
        pulse_bpm=71,
        irregular=False,
        correlation_id=correlation_id,
        raw_ref=raw_ref,
    )


async def test_external_id_type_catalogue_is_deterministic(session: AsyncSession) -> None:
    """The migration's catalogue rows carry the domain's fixed ids — in every database."""
    rows = (
        (await session.execute(select(UserExternalIdType.id, UserExternalIdType.name)))
        .tuples()
        .all()
    )
    assert {name: type_id for type_id, name in rows} == EXTERNAL_ID_TYPE_IDS


async def test_second_active_mapping_for_a_device_is_rejected(session: AsyncSession) -> None:
    """One user per device: the partial unique guards the active mapping."""
    first = await _profile(session, sub="sub-readings-2")
    second = await _profile(session, sub="sub-readings-3")
    session.add(
        UserExternalId(
            user_id=first.id, type_id=SCALE_TYPE_ID, external_id="SM5000-IB-0001", registered_at=T0
        )
    )
    await session.commit()

    session.add(
        UserExternalId(
            user_id=second.id, type_id=SCALE_TYPE_ID, external_id="SM5000-IB-0001", registered_at=T0
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_reregistration_supersedes_the_previous_mapping(session: AsyncSession) -> None:
    """Ending a mapping frees the device; history survives for audit."""
    first = await _profile(session, sub="sub-readings-4")
    second = await _profile(session, sub="sub-readings-5")
    mapping = UserExternalId(
        user_id=first.id, type_id=BP_TYPE_ID, external_id="SM5000-IB-0002", registered_at=T0
    )
    session.add(mapping)
    await session.commit()

    mapping.ended_at = T1
    session.add(
        UserExternalId(
            user_id=second.id, type_id=BP_TYPE_ID, external_id="SM5000-IB-0002", registered_at=T1
        )
    )
    await session.commit()

    mappings = (
        (
            await session.execute(
                select(UserExternalId)
                .where(UserExternalId.external_id == "SM5000-IB-0002")
                .order_by(UserExternalId.registered_at)
            )
        )
        .scalars()
        .all()
    )
    assert [m.user_id for m in mappings] == [first.id, second.id]
    assert [m.ended_at is None for m in mappings] == [False, True]


async def test_weight_reading_persists_both_unit_systems_as_supplied(
    session: AsyncSession,
) -> None:
    """The worked weight example: both unit systems, unaltered."""
    profile = await _profile(session, sub="sub-readings-6")
    raw = await _raw(session, "corr-weight-1")
    session.add(_weight_reading(profile.id, raw.id, "corr-weight-1"))
    await session.commit()

    stored = (await session.execute(select(WeightReading))).scalar_one()
    assert stored.weight_kg == Decimal("81.6")
    assert stored.weight_lbs == Decimal("180.0")
    assert stored.suspect is False
    assert stored.raw_ref == raw.id  # every row traces back to its raw payload


async def test_blood_pressure_reading_round_trip(session: AsyncSession) -> None:
    """The worked blood pressure example."""
    profile = await _profile(session, sub="sub-readings-7")
    raw = await _raw(session, "corr-bp-1")
    session.add(_bp_reading(profile.id, raw.id, "corr-bp-1"))
    await session.commit()

    stored = (await session.execute(select(BloodPressureReading))).scalar_one()
    assert (stored.systolic_mmhg, stored.diastolic_mmhg, stored.pulse_bpm) == (128, 82, 71)
    assert stored.irregular is False
    assert stored.suspect is False


async def test_duplicate_provider_reading_id_is_rejected(session: AsyncSession) -> None:
    """Provider reading ids are globally unique — the provider dedup key."""
    profile = await _profile(session, sub="sub-readings-8")
    raw_one = await _raw(session, "corr-dup-1")
    raw_two = await _raw(session, "corr-dup-2")

    session.add(_bp_reading(profile.id, raw_one.id, "corr-dup-1"))
    await session.commit()
    session.add(_bp_reading(profile.id, raw_two.id, "corr-dup-2"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_duplicate_correlation_id_is_rejected(session: AsyncSession) -> None:
    """Correlation ids are unique per reading row — the replay dedup key."""
    profile = await _profile(session, sub="sub-readings-9")
    raw = await _raw(session, "corr-replay-1")

    session.add(_weight_reading(profile.id, raw.id, "corr-replay-1", provider_reading_id="12346"))
    await session.commit()
    session.add(_weight_reading(profile.id, raw.id, "corr-replay-1", provider_reading_id="12347"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_weight_reading_without_any_measurement_is_rejected(
    session: AsyncSession,
) -> None:
    """At least one unit system must be present — an all-NULL weight row is a pipeline bug."""
    profile = await _profile(session, sub="sub-readings-10")
    raw = await _raw(session, "corr-empty-1")
    reading = _weight_reading(profile.id, raw.id, "corr-empty-1")
    reading.weight_kg = None
    reading.tare_kg = None
    reading.weight_lbs = None
    reading.tare_lbs = None
    session.add(reading)
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_second_open_quarantine_for_a_correlation_is_rejected(
    session: AsyncSession,
) -> None:
    """Redelivery before resolution must not duplicate the quarantine; history may keep
    a resolved row alongside a new open one."""
    raw = await _raw(session, "corr-quarantine-1")
    session.add(
        QuarantinedReading(
            correlation_id="corr-quarantine-1",
            raw_ref=raw.id,
            device_id="SM5000-IB-0003",
            reading_type="weight",
        )
    )
    await session.commit()

    session.add(
        QuarantinedReading(
            correlation_id="corr-quarantine-1",
            raw_ref=raw.id,
            device_id="SM5000-IB-0003",
            reading_type="weight",
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_queue_entry_starts_ready_with_zero_receives(session: AsyncSession) -> None:
    """New tickets are ready and untried; replay may re-enqueue the same correlation."""
    raw = await _raw(session, "corr-queue-1")
    session.add(ReadingQueueEntry(raw_ref=raw.id, correlation_id="corr-queue-1"))
    session.add(ReadingQueueEntry(raw_ref=raw.id, correlation_id="corr-queue-1"))
    await session.commit()

    entries = (await session.execute(select(ReadingQueueEntry))).scalars().all()
    assert len(entries) == 2  # correlation_id deliberately not unique here — replay
    # re-enqueues the same correlation as a fresh entry
    assert all(e.state == "ready" and e.receive_count == 0 for e in entries)
