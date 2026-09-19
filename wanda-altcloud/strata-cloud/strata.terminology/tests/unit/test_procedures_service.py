"""Unit tests for the procedure search seam — drives ``search_procedures`` directly.

Runs against the seeded test database (see conftest). Expectations track the committed
fixture ``sample-icd10pcs.txt``: four heart-assist codes (02HA0QZ/RJ/RS/RZ) plus one
flag-``0`` table header ("Central Nervous System and Cranial Nerves, Bypass") that must
never be returned.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from strata_terminology.services.procedures import search_procedures

pytestmark = pytest.mark.anyio


async def test_matches_long_title_substring(session: AsyncSession) -> None:
    """Procedure search matches a long-title substring across the fixture codes."""
    codes = {p.code for p in await search_procedures(session, "heart", limit=20)}
    assert codes == {"02HA0QZ", "02HA0RJ", "02HA0RS", "02HA0RZ"}


async def test_matches_code_prefix(session: AsyncSession) -> None:
    """Procedure search matches a code prefix."""
    results = await search_procedures(session, "02HA0Q", limit=20)
    assert [p.code for p in results] == ["02HA0QZ"]


async def test_exact_code_ranks_first(session: AsyncSession) -> None:
    # "02HA0RZ" is an exact code match; it must lead even though other codes share the prefix.
    """An exact procedure code match ranks first among shared-prefix results."""
    results = await search_procedures(session, "02HA0RZ", limit=20)
    assert results[0].code == "02HA0RZ"


async def test_header_rows_are_excluded(session: AsyncSession) -> None:
    # The flag-0 header is the only row containing this phrase; it must not be returned.
    """Non-billable header rows (flag 0) are never returned."""
    assert await search_procedures(session, "Central Nervous System", limit=20) == []


async def test_blank_query_returns_nothing(session: AsyncSession) -> None:
    """A blank procedure query returns nothing."""
    assert await search_procedures(session, "   ", limit=20) == []


async def test_limit_is_respected(session: AsyncSession) -> None:
    """The procedure search limit caps the result count."""
    assert len(await search_procedures(session, "heart", limit=2)) == 2
