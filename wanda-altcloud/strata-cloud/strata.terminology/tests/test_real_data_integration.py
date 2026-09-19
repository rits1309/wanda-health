"""Real-data integration tests (opt-in) — the genuine end-to-end path.

Unlike the rest of the suite (which seeds tiny, deterministic fixtures into an ephemeral
Postgres), this proves the *real* pipeline: fetch the live FDA/CMS sources, ingest them, and
search the resulting rows through the service seams — exactly what ``inv refresh-*`` does, but
asserted. It therefore needs **Docker** (a Postgres container) *and* **network** (the openFDA
manifest + the CMS/openFDA downloads); it skips cleanly when either is missing.

Marked ``external`` (see pyproject markers) — a distinct class from the hermetic ``integration``
tests (which spin only a local container): this one reaches *live* third-party endpoints, so it
is excluded from the default run / CI and is opt-in. Run it with::

    inv test-realdata            # pytest -m external

It is deliberately slow (downloads ~28 MB and ingests ~136k medication + ~79k procedure +
~75k diagnosis rows) and depends on FDA/CMS availability, which is why it never gates a push.
Assertions target values stable across annual data refreshes (a generic drug name, a stable
ICD-10 code) rather than exact counts, which drift as the upstream data changes.
"""

from __future__ import annotations

import asyncio
import tempfile
import urllib.error
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import TypeVar

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from strata_core.migrations import upgrade_to_head

from scripts.fetch import _download
from scripts.ingest_diagnoses import ingest as ingest_diagnoses
from scripts.ingest_drugs import ingest as ingest_drugs
from scripts.ingest_procedures import ingest as ingest_procedures
from scripts.sources import get_source
from strata_terminology.core.config import settings
from strata_terminology.services.diagnoses import search_diagnoses
from strata_terminology.services.medications import search_medications
from strata_terminology.services.procedures import search_procedures

pytestmark = pytest.mark.external

# Each domain's real source and the loader that ingests it (all read settings.database_url).
_DOMAINS = (
    ("drugs", ingest_drugs),
    ("procedures", ingest_procedures),
    ("diagnoses", ingest_diagnoses),
)


@pytest.fixture(scope="module")
def real_data_factory() -> Iterator[async_sessionmaker[AsyncSession]]:
    """Provision a Postgres with the *real* fetched data; yield a session factory.

    Its own container (separate from the fixture-seeded suite DB), so the two never interfere.
    Skips if Docker is down, or if the network is unavailable for the fetch.
    """
    from testcontainers.core.docker_client import DockerClient

    try:
        DockerClient().client.ping()
    except Exception:  # any Docker-client failure means "no Docker" → skip, don't fail
        pytest.skip("Docker not available for the real-data integration test")

    from testcontainers.postgres import PostgresContainer

    previous_url = settings.database_url
    with (
        PostgresContainer("postgres:16", driver="asyncpg") as pg,
        tempfile.TemporaryDirectory() as tmp,
    ):
        settings.database_url = pg.get_connection_url()
        upgrade_to_head(settings.database_url)  # the canonical migrations own the schema
        engine = create_async_engine(settings.database_url, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async def _provision() -> None:
            for name, ingest in _DOMAINS:
                source = get_source(name)
                for url in source.resolve_urls():
                    dest = Path(tmp) / source.dest_name(url)
                    _download(url, dest)  # streams from openFDA/CMS
                    await ingest(dest)

        try:
            asyncio.run(_provision())
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            pytest.skip(f"network unavailable for the real-data fetch: {exc}")

        try:
            yield factory
        finally:
            asyncio.run(engine.dispose())
    settings.database_url = previous_url


_T = TypeVar("_T")


def _run(
    factory: async_sessionmaker[AsyncSession],
    seam: Callable[..., Awaitable[list[_T]]],
    query: str,
) -> list[_T]:
    """Call a search seam against the real-data factory in a fresh event loop."""

    async def _call() -> list[_T]:
        async with factory() as session:
            return await seam(session, query, limit=20)

    return asyncio.run(_call())


def test_medications_search_real_data(
    real_data_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Brand search resolves to the known generic in the live NDC data.
    """Against the live NDC refresh, a brand search resolves to the known generic."""
    zepbound = _run(real_data_factory, search_medications, "zepbound")
    assert zepbound, "no medication results for 'zepbound' in the real NDC data"
    assert any(m.generic_name == "tirzepatide" for m in zepbound)

    # A generic-name search fans out across multiple brands/products.
    tirzepatide = _run(real_data_factory, search_medications, "tirzepatide")
    assert len({m.brand_name for m in tirzepatide}) >= 2


def test_procedures_search_real_data(
    real_data_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Free-text title search.
    """Against the live ICD-10-PCS refresh, a title search returns real procedures."""
    heart = _run(real_data_factory, search_procedures, "heart")
    assert heart, "no procedure results for 'heart' in the real ICD-10-PCS data"
    assert any("heart" in p.long_title.lower() for p in heart)

    # Code-prefix search — the top hit is ranked as a code-prefix match.
    prefixed = _run(real_data_factory, search_procedures, "0210")
    assert prefixed and prefixed[0].code.startswith("0210")


def test_diagnoses_search_real_data(
    real_data_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Free-text title search.
    """Against the live ICD-10-CM refresh, title and dotted-code searches resolve."""
    hypertension = _run(real_data_factory, search_diagnoses, "hypertension")
    assert any("hypertension" in d.long_title.lower() for d in hypertension)

    # A user-typed decimal is stripped: "G93.2" resolves to the stored code "G932".
    decimal = _run(real_data_factory, search_diagnoses, "G93.2")
    assert decimal and decimal[0].code == "G932"
