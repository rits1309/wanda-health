"""Ingest the FDA NDC directory into PostgreSQL.

Replaces the old ``build_drug_index.py`` (which flattened the source to a JSON file). This
streams the openFDA NDC export and loads it into the normalized schema: one ``products`` row
per record plus its ``active_ingredients`` / ``packagings`` / ``product_identifiers`` children,
with the low-cardinality ``route`` kept as an array, ``pharm_class`` parsed to JSONB, and the
full source record stored in ``raw`` (lossless).

Idempotent: products upsert on the natural key ``product_id`` and their children are replaced,
so re-running refreshes in place. The connection comes from ``STRATA_DATABASE_URL``.

Usage (run as a module so the shared ``scripts.sources`` import resolves):
    python -m scripts.ingest_drugs            # the fetched file(s) in var/ (see inv fetch-drugs)
    python -m scripts.ingest_drugs some.zip   # an explicit .json / .json.zip path
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import delete, insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.sql.functions import now
from strata_core.domains.reference import ActiveIngredient, Packaging, Product, ProductIdentifier

from scripts.sources import get_source
from strata_terminology.core.config import settings

# Products use a multi-row VALUES upsert (needs RETURNING), so keep batches well under
# Postgres' 65535-bound-parameter limit; children use executemany and are unconstrained.
BATCH = 1000

# openfda sub-keys that are cross-system identifiers (vs. names/flags/pharm_class arrays).
_IDENTIFIER_SYSTEMS = ("rxcui", "unii", "spl_set_id", "upc", "nui")

# pharm_class strings look like "Angiotensin 2 Receptor Blocker [EPC]".
_PHARM_CLASS_RE = re.compile(r"^(.*?)\s*\[(\w+)\]\s*$")

# Product columns refreshed on re-ingest (everything except id / product_id / ingested_at).
_PRODUCT_UPDATE_COLS = (
    "product_ndc",
    "brand_name",
    "brand_name_base",
    "generic_name",
    "labeler_name",
    "dosage_form",
    "product_type",
    "marketing_category",
    "marketing_start_date",
    "marketing_end_date",
    "listing_expiration_date",
    "dea_schedule",
    "application_number",
    "spl_id",
    "finished",
    "routes",
    "pharm_classes",
    "raw",
)


def _read(source: Path) -> str:
    """Return the JSON text from a ``.json`` file or the first ``.json`` inside a ``.zip``."""
    if source.suffix == ".zip":
        with zipfile.ZipFile(source) as zf:
            name = next(n for n in zf.namelist() if n.endswith(".json"))
            return zf.read(name).decode("utf-8")
    return source.read_text(encoding="utf-8")


def _parse_date(value: Any) -> date | None:
    """Parse an FDA ``YYYYMMDD`` date; return None for missing/malformed values."""
    if not value:
        return None
    s = str(value).strip()
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def _pharm_classes(values: Any) -> list[dict[str, str]] | None:
    """Parse ``["Name [EPC]", ...]`` into ``[{"name": "Name", "type": "EPC"}, ...]``."""
    if not values:
        return None
    out: list[dict[str, str]] = []
    for v in values:
        m = _PHARM_CLASS_RE.match(str(v))
        out.append({"name": m.group(1), "type": m.group(2)} if m else {"name": str(v), "type": ""})
    return out


def _product_row(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_id": p["product_id"],
        "product_ndc": p.get("product_ndc", ""),
        "brand_name": p.get("brand_name"),
        "brand_name_base": p.get("brand_name_base"),
        "generic_name": p.get("generic_name"),
        "labeler_name": p.get("labeler_name"),
        "dosage_form": p.get("dosage_form"),
        "product_type": p.get("product_type"),
        "marketing_category": p.get("marketing_category"),
        "marketing_start_date": _parse_date(p.get("marketing_start_date")),
        "marketing_end_date": _parse_date(p.get("marketing_end_date")),
        "listing_expiration_date": _parse_date(p.get("listing_expiration_date")),
        "dea_schedule": p.get("dea_schedule"),
        "application_number": p.get("application_number"),
        "spl_id": p.get("spl_id"),
        "finished": p.get("finished"),
        "routes": p.get("route") or None,
        "pharm_classes": _pharm_classes(p.get("pharm_class")),
        "raw": p,
    }


async def _ingest_batch(
    session: AsyncSession, batch: list[dict[str, Any]], counts: dict[str, int]
) -> None:
    """Upsert one batch of products and replace their children."""
    ins = pg_insert(Product)
    stmt = (
        ins.values([_product_row(p) for p in batch])
        .on_conflict_do_update(
            index_elements=["product_id"],
            set_={c: getattr(ins.excluded, c) for c in _PRODUCT_UPDATE_COLS}
            | {"ingested_at": now()},
        )
        .returning(Product.id, Product.product_id)
    )
    result = await session.execute(stmt)
    pk_by_product_id: dict[str, int] = {pid: pk for pk, pid in result.all()}
    counts["products"] += len(pk_by_product_id)

    # Replace children so a re-ingest can't leave stale rows behind.
    pks = list(pk_by_product_id.values())
    for model in (ActiveIngredient, Packaging, ProductIdentifier):
        await session.execute(delete(model).where(model.product_pk.in_(pks)))

    ai_rows: list[dict[str, Any]] = []
    pkg_rows: list[dict[str, Any]] = []
    ident_rows: list[dict[str, Any]] = []
    for p in batch:
        pk = pk_by_product_id[p["product_id"]]
        for i, ai in enumerate(p.get("active_ingredients") or []):
            ai_rows.append(
                {
                    "product_pk": pk,
                    "ord": i,
                    "name": ai.get("name", ""),
                    "strength": ai.get("strength"),
                }
            )
        for pkg in p.get("packaging") or []:
            pkg_rows.append(
                {
                    "product_pk": pk,
                    "package_ndc": pkg.get("package_ndc", ""),
                    "description": pkg.get("description"),
                    "marketing_start_date": _parse_date(pkg.get("marketing_start_date")),
                    "marketing_end_date": _parse_date(pkg.get("marketing_end_date")),
                    "sample": pkg.get("sample"),
                }
            )
        openfda = p.get("openfda") or {}
        for system in _IDENTIFIER_SYSTEMS:
            for value in openfda.get(system) or []:
                ident_rows.append({"product_pk": pk, "system": system, "value": str(value)})

    if ai_rows:
        await session.execute(insert(ActiveIngredient), ai_rows)
        counts["active_ingredients"] += len(ai_rows)
    if pkg_rows:
        await session.execute(insert(Packaging), pkg_rows)
        counts["packagings"] += len(pkg_rows)
    if ident_rows:
        await session.execute(insert(ProductIdentifier), ident_rows)
        counts["identifiers"] += len(ident_rows)


async def ingest(source: Path) -> dict[str, int]:
    raw = json.loads(_read(source))
    products = raw.get("results", raw if isinstance(raw, list) else [])
    products = [p for p in products if p.get("product_id")]
    counts = {"products": 0, "active_ingredients": 0, "packagings": 0, "identifiers": 0}

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            for start in range(0, len(products), BATCH):
                await _ingest_batch(session, products[start : start + BATCH], counts)
                await session.commit()
                print(
                    f"  ...{min(start + BATCH, len(products))}/{len(products)} products", end="\r"
                )
    finally:
        await engine.dispose()
    print()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        type=Path,
        nargs="?",
        help="Path to an FDA NDC .json / .json.zip; defaults to the fetched file(s) in var/",
    )
    args = parser.parse_args()
    sources = [args.source] if args.source else get_source("drugs").paths()
    if not sources:
        raise SystemExit("no FDA NDC source found in var/ — run `inv fetch-drugs` first")

    total = {"products": 0, "active_ingredients": 0, "packagings": 0, "identifiers": 0}
    for src in sources:
        counts = asyncio.run(ingest(src))
        for key in total:
            total[key] += counts[key]
    print(
        "Ingested "
        f"{total['products']} products, {total['active_ingredients']} active ingredients, "
        f"{total['packagings']} packagings, {total['identifiers']} identifiers."
    )


if __name__ == "__main__":
    main()
