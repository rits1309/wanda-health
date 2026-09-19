"""Ingest the CMS ICD-10-CM order file into PostgreSQL.

Reads the fixed-width order file (a ``.txt`` or the ``*order*.txt`` inside the CMS ``.zip``),
keeps the valid billable codes (flag ``1``), and upserts them into ``diagnoses`` on the
natural key ``code`` — so re-running refreshes in place. Connection from ``STRATA_DATABASE_URL``.

Usage (run as a module so the shared ``scripts.*`` imports resolve):
    python -m scripts.ingest_diagnoses            # the fetched file in var/ (inv fetch-diagnoses)
    python -m scripts.ingest_diagnoses some.zip   # an explicit order .txt / CMS .zip path
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.sql.functions import now
from strata_core.domains.reference import Diagnosis

from scripts._icd10_order import parse_order_lines, read_order_file
from scripts.sources import get_source
from strata_terminology.core.config import settings

# Multi-row VALUES upsert needs to stay well under Postgres' 65535-bound-parameter limit.
BATCH = 1000
_UPDATE_COLS = ("short_title", "long_title", "order_number")


async def _ingest_batch(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    ins = pg_insert(Diagnosis)
    stmt = ins.values(rows).on_conflict_do_update(
        index_elements=["code"],
        set_={c: getattr(ins.excluded, c) for c in _UPDATE_COLS} | {"ingested_at": now()},
    )
    await session.execute(stmt)


async def ingest(source: Path) -> int:
    rows: list[dict[str, Any]] = [
        {
            "code": r.code,
            "short_title": r.short_title,
            "long_title": r.long_title,
            "order_number": r.order_number,
        }
        for r in parse_order_lines(read_order_file(source))
        if r.is_valid
    ]

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            for start in range(0, len(rows), BATCH):
                await _ingest_batch(session, rows[start : start + BATCH])
                await session.commit()
                print(f"  ...{min(start + BATCH, len(rows))}/{len(rows)} diagnoses", end="\r")
    finally:
        await engine.dispose()
    print()
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        type=Path,
        nargs="?",
        help="Path to an ICD-10-CM order .txt / CMS .zip; defaults to the fetched file in var/",
    )
    args = parser.parse_args()
    sources = [args.source] if args.source else get_source("diagnoses").paths()
    if not sources:
        raise SystemExit("no ICD-10-CM source found in var/ — run `inv fetch-diagnoses` first")
    count = sum(asyncio.run(ingest(src)) for src in sources)
    print(f"Ingested {count} diagnoses.")


if __name__ == "__main__":
    main()
