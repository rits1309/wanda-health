"""Smoke test: the async DB wiring works against the ephemeral testcontainers Postgres."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.anyio
async def test_session_executes_against_postgres(session: AsyncSession) -> None:
    """The API test session executes against the real Postgres."""
    assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
