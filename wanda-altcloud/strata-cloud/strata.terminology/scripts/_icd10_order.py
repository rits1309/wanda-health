"""Parser for the CMS ICD-10 fixed-width "order" files — shared by procedures & diagnoses.

The ICD-10-PCS and ICD-10-CM order files CMS publishes use an identical fixed-width layout,
one row per code:

    cols  1-5   order number (5-digit)
    cols  7-13  code (≤7 chars, left-justified, stored without a decimal point)
    col   15    flag: '1' = a valid/billable (selectable) code, '0' = a table header
    cols 17-76  short (abbreviated) title
    cols 78+    long title

This module is deliberately domain-free: it turns a file into rows. The procedure and
diagnosis ingest scripts decide what to keep (both take only ``is_valid`` rows).
"""

from __future__ import annotations

import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OrderRow:
    order_number: int
    code: str
    is_valid: bool  # True = billable/selectable code; False = a non-selectable table header
    short_title: str
    long_title: str


def parse_order_lines(text: str) -> Iterator[OrderRow]:
    """Yield one ``OrderRow`` per non-blank line of a CMS ICD-10 order file."""
    for line in text.splitlines():
        if not line.strip():
            continue
        yield OrderRow(
            order_number=int(line[0:5]),
            code=line[6:13].strip(),
            is_valid=line[14:15] == "1",
            short_title=line[16:76].strip(),
            long_title=line[77:].strip(),
        )


def read_order_file(source: Path) -> str:
    """Return order-file text from a ``.txt`` or the ``*order*.txt`` (not addenda) in a ``.zip``."""
    if source.suffix == ".zip":
        with zipfile.ZipFile(source) as zf:
            name = next(
                n
                for n in zf.namelist()
                if n.endswith(".txt") and "order" in n.lower() and "addenda" not in n.lower()
            )
            return zf.read(name).decode("utf-8")
    return source.read_text(encoding="utf-8")
