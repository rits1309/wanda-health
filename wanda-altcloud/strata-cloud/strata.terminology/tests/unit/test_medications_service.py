"""Unit tests for the search logic (the seam) — drives ``search_medications`` directly.

Runs against the seeded test database (see conftest). Expectations track the committed
fixture: the GLP-1 set (Wegovy/Ozempic/Rybelsus = semaglutide, Zepbound/Mounjaro =
tirzepatide, Saxenda = liraglutide, Trulicity = dulaglutide) plus Lipitor.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from strata_terminology.services.medications import search_medications

pytestmark = pytest.mark.anyio


async def test_matches_brand_name_case_insensitively(session: AsyncSession) -> None:
    """Medication search matches brand names case-insensitively."""
    results = await search_medications(session, "wegovy", limit=20)
    assert [m.brand_name for m in results] == ["WEGOVY"]


async def test_matches_generic_name_returns_all_brands(session: AsyncSession) -> None:
    # semaglutide is Wegovy, Ozempic and Rybelsus ("oral semaglutide") in the fixture.
    """A generic-name search returns every brand carrying that ingredient."""
    brands = {m.brand_name for m in await search_medications(session, "semaglutide", limit=20)}
    assert brands == {"WEGOVY", "Ozempic", "RYBELSUS"}


async def test_prefix_match_ranks_before_midstring_match(session: AsyncSession) -> None:
    # "lir" prefixes "liraglutide" (Saxenda); it must precede any mid-string match.
    """A prefix match ranks before any mid-string match."""
    results = await search_medications(session, "lir", limit=20)
    assert results[0].brand_name == "Saxenda"


async def test_strength_is_built_from_active_ingredients(session: AsyncSession) -> None:
    # The single-ingredient Zepbound carries its active-ingredient strength as a string.
    """A medication's strength string is built from its active ingredients."""
    [zepbound] = await search_medications(session, "zepbound", limit=20)
    assert zepbound.strength and zepbound.generic_name == "tirzepatide"


async def test_routes_and_ingredients_are_returned(session: AsyncSession) -> None:
    #: Summit's picker renders route + per-ingredient detail, so the API carries
    # them (source order; strength per ingredient).
    """Routes and per-ingredient detail ride the result for Summit's picker."""
    [zepbound] = await search_medications(session, "zepbound", limit=20)
    assert zepbound.routes == ["SUBCUTANEOUS"]
    assert [(i.name, i.strength) for i in zepbound.ingredients] == [("TIRZEPATIDE", "25 mg/mL")]


async def test_blank_query_returns_nothing(session: AsyncSession) -> None:
    """A blank medication query returns nothing."""
    assert await search_medications(session, "   ", limit=20) == []


async def test_limit_is_respected(session: AsyncSession) -> None:
    """The medication search limit caps the result count."""
    assert len(await search_medications(session, "e", limit=2)) == 2
