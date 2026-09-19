"""The canonical migration history — single head, lossless round-trip, no drift.

These are the CI checks the schema discipline demands, run against an ephemeral Postgres:
the full history applies to an empty database; upgrade → downgrade → upgrade is
lossless in structure; the role catalogue is re-seeded; there is exactly one
head; and autogenerate against the migrated database produces an **empty** diff
(models ↔ migrations never drift).

Sync tests on purpose: alembic/env.py drives its own asyncio.run, so it cannot
be invoked from inside a running event loop.
"""

import asyncio
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import create_async_engine

import strata_core.domains  # noqa: F401  — registers every canonical model
from alembic import command
from strata_core.db.base import Base
from strata_core.domains.kernel import EXTERNAL_ID_TYPE_IDS, LANGUAGE_CODES, ROLE_NAMES
from strata_core.migrations import alembic_config
from strata_core.ownership import TABLE_DOMAINS

pytestmark = pytest.mark.integration


def _fetch_state(database_url: str) -> tuple[list[str], list[str], dict[str, str], list[str]]:
    async def inner() -> tuple[list[str], list[str], dict[str, str], list[str]]:
        engine = create_async_engine(database_url)
        async with engine.connect() as conn:
            tables = (
                (
                    await conn.execute(
                        text(
                            "select table_name from information_schema.tables"
                            " where table_schema = 'public' order by table_name"
                        )
                    )
                )
                .scalars()
                .all()
            )
            roles: list[str] = []
            if "roles" in tables:
                roles = list(
                    (await conn.execute(text("select name from roles order by name")))
                    .scalars()
                    .all()
                )
            external_id_types: dict[str, str] = {}
            if "user_external_id_types" in tables:
                rows = await conn.execute(text("select name, id from user_external_id_types"))
                external_id_types = dict(rows.tuples().all())
            languages: list[str] = []
            if "languages" in tables:
                languages = list(
                    (await conn.execute(text("select code from languages order by code")))
                    .scalars()
                    .all()
                )
        await engine.dispose()
        return list(tables), list(roles), external_id_types, languages

    return asyncio.run(inner())


def _compare_against_models(database_url: str) -> list[Any]:
    def compare(conn: Connection) -> list[Any]:
        ctx = MigrationContext.configure(
            conn,
            opts={
                "compare_type": True,
                "include_name": _include_default_schema_only,
            },
        )
        return list(compare_metadata(ctx, Base.metadata))

    async def inner() -> list[Any]:
        engine = create_async_engine(database_url)
        async with engine.connect() as conn:
            diff = await conn.run_sync(compare)
        await engine.dispose()
        return diff

    return asyncio.run(inner())


def _include_default_schema_only(
    name: str | None, type_: str, parent_names: dict[str, Any]
) -> bool:
    return name is None if type_ == "schema" else True


def test_single_head() -> None:
    """The Alembic script directory has exactly one head, never a split history."""
    script = ScriptDirectory.from_config(alembic_config("postgresql+asyncpg://unused/unused"))
    assert len(script.get_heads()) == 1


def test_url_encoded_credentials_survive_the_config() -> None:
    """`%` in the URL (URL-encoded password) must not trip the ini layer's
    interpolating ConfigParser — it raised at set_main_option before the escape."""
    raw = "postgresql+asyncpg://user:p%40ss@host:5432/db"
    cfg = alembic_config(raw)
    assert cfg.attributes["sqlalchemy_url"] == raw  # the channel env.py reads first
    assert cfg.get_main_option("sqlalchemy.url") == raw  # unescaped on the way out


def test_migration_round_trip_reseeds_the_catalogue(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A head upgrade creates the owned tables and reseeds roles, external-id types, languages."""
    monkeypatch.setenv("STRATA_DATABASE_URL", database_url)
    cfg = alembic_config(database_url)

    command.upgrade(cfg, "head")
    tables, roles, external_id_types, languages = _fetch_state(database_url)
    assert set(tables) == {"alembic_version", *TABLE_DOMAINS}
    assert roles == sorted(ROLE_NAMES)
    assert external_id_types == EXTERNAL_ID_TYPE_IDS
    assert languages == sorted(LANGUAGE_CODES)

    command.downgrade(cfg, "base")
    tables, _, _, _ = _fetch_state(database_url)
    assert tables == ["alembic_version"]  # the chain's own bookkeeping survives

    command.upgrade(cfg, "head")
    _, roles, external_id_types, languages = _fetch_state(database_url)
    assert roles == sorted(ROLE_NAMES)
    assert external_id_types == EXTERNAL_ID_TYPE_IDS
    assert languages == sorted(LANGUAGE_CODES)

    command.downgrade(cfg, "base")


def test_language_normalisation_migrates_data_losslessly(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Data fidelity: rows written on the old shape (``languages`` ARRAY +
    member ``preferred_language`` string) survive the normalisation — arrays become
    link rows, the member preferred language lands on the profile FK, an
    out-of-catalogue code found in the data joins the catalogue instead of failing
    the FK, and the downgrade rebuilds the old shape."""
    monkeypatch.setenv("STRATA_DATABASE_URL", database_url)
    cfg = alembic_config(database_url)
    command.upgrade(cfg, "aedf37dd40b7")  # the pre-normalisation head

    async def run_sql(statements: list[str]) -> list[Any]:
        engine = create_async_engine(database_url)
        results: list[Any] = []
        async with engine.begin() as conn:
            for statement in statements:
                results.append(await conn.execute(text(statement)))
        await engine.dispose()
        return results

    asyncio.run(
        run_sql(
            [
                "insert into user_profiles"
                " (id, cognito_sub, email, display_name, timezone, languages) values"
                " ('p-1', 'sub-1', 'one@example.test', 'One', 'Europe/London', '{en,es}'),"
                " ('p-2', 'sub-2', 'two@example.test', 'Two', 'Europe/London', '{fr}')",
                "insert into member_details (user_id, preferred_language) values ('p-1', 'es')",
            ]
        )
    )

    # This is the LANGUAGES-normalisation fidelity test, so it targets that
    # revision rather than "head": member_details is dropped by a later revision,
    # so its data cannot round-trip through head — the drop is
    # covered by the generic upgrade/downgrade round-trip test instead.
    command.upgrade(cfg, "5cd17e752ff3")
    links, preferred, catalogue = asyncio.run(
        run_sql(
            [
                "select user_id, language_code from user_languages order by user_id, language_code",
                "select id, preferred_language_code from user_profiles order by id",
                "select code from languages order by code",
            ]
        )
    )
    assert links.tuples().all() == [("p-1", "en"), ("p-1", "es"), ("p-2", "fr")]
    assert preferred.tuples().all() == [("p-1", "es"), ("p-2", None)]
    assert catalogue.scalars().all() == ["en", "es", "fr"]

    command.downgrade(cfg, "aedf37dd40b7")
    arrays, member_preferred = asyncio.run(
        run_sql(
            [
                "select id, languages from user_profiles order by id",
                "select user_id, preferred_language from member_details order by user_id",
            ]
        )
    )
    assert arrays.tuples().all() == [("p-1", ["en", "es"]), ("p-2", ["fr"])]
    assert member_preferred.tuples().all() == [("p-1", "es")]

    command.downgrade(cfg, "base")


def test_name_split_backfill_is_lossless(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Data fidelity: the 8c41d7b02a96 revision backfills first/last by
    splitting the historical display name on the FIRST space — a single token
    copies into both halves — and keeps every existing display name as an
    override (identical rendering). The downgrade refills a NULL display
    name from "First Last" before restoring NOT NULL."""
    monkeypatch.setenv("STRATA_DATABASE_URL", database_url)
    cfg = alembic_config(database_url)
    command.upgrade(cfg, "5cd17e752ff3")  # the pre-names head

    async def run_sql(statements: list[str]) -> list[Any]:
        engine = create_async_engine(database_url)
        results: list[Any] = []
        async with engine.begin() as conn:
            for statement in statements:
                results.append(await conn.execute(text(statement)))
        await engine.dispose()
        return results

    asyncio.run(
        run_sql(
            [
                "insert into user_profiles (id, email, display_name, timezone) values"
                " ('n-1', 'one@example.test', 'Sarah Mitchell', 'Etc/UTC'),"
                " ('n-2', 'two@example.test', 'Sarah Jane Smith', 'Etc/UTC'),"
                " ('n-3', 'three@example.test', 'Cher', 'Etc/UTC')",
            ]
        )
    )

    command.upgrade(cfg, "head")
    (names,) = asyncio.run(
        run_sql(
            [
                "select id, first_name, last_name, display_name from user_profiles order by id",
            ]
        )
    )
    assert names.tuples().all() == [
        ("n-1", "Sarah", "Mitchell", "Sarah Mitchell"),  # split on the space
        ("n-2", "Sarah", "Jane Smith", "Sarah Jane Smith"),  # FIRST space only
        ("n-3", "Cher", "Cher", "Cher"),  # single token → both halves
    ]

    # A post-upgrade row with no override exercises the downgrade refill.
    asyncio.run(
        run_sql(
            [
                "insert into user_profiles (id, email, first_name, last_name, timezone) values"
                " ('n-4', 'four@example.test', 'Miles', 'Davis', 'Etc/UTC')",
            ]
        )
    )
    command.downgrade(cfg, "5cd17e752ff3")
    (display,) = asyncio.run(run_sql(["select id, display_name from user_profiles order by id"]))
    assert display.tuples().all() == [
        ("n-1", "Sarah Mitchell"),
        ("n-2", "Sarah Jane Smith"),
        ("n-3", "Cher"),
        ("n-4", "Miles Davis"),  # the fallback, materialised on the way down
    ]

    command.downgrade(cfg, "base")


def test_no_model_to_migration_drift(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Autogenerate against the migrated database must find nothing to do."""
    monkeypatch.setenv("STRATA_DATABASE_URL", database_url)
    cfg = alembic_config(database_url)
    command.upgrade(cfg, "head")
    try:
        diff = _compare_against_models(database_url)
        assert diff == [], f"model ↔ migration drift detected: {diff}"
    finally:
        command.downgrade(cfg, "base")


def test_cognito_sub_backfills_into_the_mapping(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The c3a91e40d5b8 revision is lossless for existing profiles: every
    pre-migration ``cognito_sub`` value becomes an ACTIVE ``Cognito Sub``
    mapping row on the same profile."""
    monkeypatch.setenv("STRATA_DATABASE_URL", database_url)
    cfg = alembic_config(database_url)
    command.upgrade(cfg, "aedf37dd40b7")  # the pre-identity-mapping head

    async def seed_old_shape() -> None:
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO user_profiles"
                    " (id, cognito_sub, email, display_name, timezone, languages)"
                    " VALUES ('p-backfill', 'sub-backfill', 'b@wanda.test', 'B',"
                    " 'Etc/UTC', '{}')"
                )
            )
        await engine.dispose()

    asyncio.run(seed_old_shape())
    command.upgrade(cfg, "head")

    async def fetch_mapping() -> tuple[list[tuple[str, str]], list[str]]:
        engine = create_async_engine(database_url)
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT user_id, external_id FROM user_external_ids"
                        " WHERE type_id = 'extid-cognito-sub' AND ended_at IS NULL"
                    )
                )
            ).all()
            columns = (
                (
                    await conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns"
                            " WHERE table_name = 'user_profiles'"
                        )
                    )
                )
                .scalars()
                .all()
            )
        await engine.dispose()
        return [tuple(r) for r in rows], list(columns)

    try:
        mappings, columns = asyncio.run(fetch_mapping())
        assert mappings == [("p-backfill", "sub-backfill")]
        assert "cognito_sub" not in columns
    finally:
        command.downgrade(cfg, "base")


def test_legacy_id_rows_retype_losslessly_into_the_summit_split(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The a1127b8feee7 revision repoints EVERY ``Legacy ID`` mapping — active
    and ended alike (history is never deleted) — onto the renamed
    ``Legacy Summit Django User ID`` type, and removes the old catalogue row."""
    monkeypatch.setenv("STRATA_DATABASE_URL", database_url)
    cfg = alembic_config(database_url)
    command.upgrade(cfg, "c3a91e40d5b8")  # the pre-split head: Legacy ID exists

    async def seed_pre_split() -> None:
        engine = create_async_engine(database_url)
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO user_profiles"
                    " (id, email, display_name, timezone, languages)"
                    " VALUES ('p-retype', NULL, 'R', 'Etc/UTC', '{}')"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO user_external_ids"
                    " (id, user_id, type_id, external_id, registered_at, ended_at,"
                    "  created_at, updated_at)"
                    " VALUES"
                    " ('x-active', 'p-retype', 'extid-legacy-id', '2253', now(), NULL,"
                    "  now(), now()),"
                    " ('x-ended', 'p-retype', 'extid-legacy-id', '9999', now(), now(),"
                    "  now(), now())"
                )
            )
        await engine.dispose()

    asyncio.run(seed_pre_split())
    command.upgrade(cfg, "head")

    async def fetch_split_state() -> tuple[list[tuple[str, str, bool]], list[str]]:
        engine = create_async_engine(database_url)
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT id, type_id, ended_at IS NULL FROM user_external_ids"
                        " WHERE user_id = 'p-retype' ORDER BY id"
                    )
                )
            ).all()
            type_ids = (
                (await conn.execute(text("SELECT id FROM user_external_id_types"))).scalars().all()
            )
        await engine.dispose()
        return [tuple(r) for r in rows], list(type_ids)

    try:
        rows, type_ids = asyncio.run(fetch_split_state())
        assert rows == [
            ("x-active", "extid-legacy-summit-django-user-id", True),
            ("x-ended", "extid-legacy-summit-django-user-id", False),  # history intact
        ]
        assert "extid-legacy-id" not in type_ids
        assert {"extid-legacy-summit-patient-id", "extid-legacy-summit-coach-id"} <= set(type_ids)
    finally:
        command.downgrade(cfg, "base")
