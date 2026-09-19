"""Unit tests for the diagnosis search seam — drives ``search_diagnoses`` directly.

Runs against the seeded test database (see conftest). Expectations track the committed
fixture ``sample-icd10cm.txt``: G932 (benign intracranial hypertension), the four H3503x
hypertensive-retinopathy codes, I10 (essential hypertension), plus one flag-``0`` header
("Cholera", A00) that must never be returned.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from strata_terminology.services.diagnoses import search_diagnoses

pytestmark = pytest.mark.anyio


async def test_matches_long_title_substring(session: AsyncSession) -> None:
    """Diagnosis search matches a long-title substring across the fixture codes."""
    codes = {d.code for d in await search_diagnoses(session, "hyperten", limit=20)}
    assert codes == {"G932", "H35031", "H35032", "H35033", "H35039", "I10"}


async def test_matches_code_prefix(session: AsyncSession) -> None:
    """Diagnosis search matches a code prefix."""
    codes = {d.code for d in await search_diagnoses(session, "H3503", limit=20)}
    assert codes == {"H35031", "H35032", "H35033", "H35039"}


async def test_user_typed_decimal_is_stripped(session: AsyncSession) -> None:
    # Codes are stored without a decimal, so "G93.2" must still find G932.
    """A user-typed decimal (G93.2) is stripped to match the stored undotted code."""
    results = await search_diagnoses(session, "G93.2", limit=20)
    assert [d.code for d in results] == ["G932"]


async def test_header_rows_are_excluded(session: AsyncSession) -> None:
    # The flag-0 header "Cholera" (A00) is the only row with that text; never returned.
    """Non-billable header rows (flag 0) are never returned."""
    assert await search_diagnoses(session, "Cholera", limit=20) == []


async def test_blank_query_returns_nothing(session: AsyncSession) -> None:
    """A blank diagnosis query returns nothing."""
    assert await search_diagnoses(session, "   ", limit=20) == []


async def test_limit_is_respected(session: AsyncSession) -> None:
    """The diagnosis search limit caps the result count."""
    assert len(await search_diagnoses(session, "hyperten", limit=2)) == 2
