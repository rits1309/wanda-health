"""Registry of Strata's reference-data sources — the single place that knows *where each
source lives* and *how to fetch it*.

Medications, procedures and diagnoses share one uniform lifecycle — **fetch → ingest → clean**
— run through ``inv`` (see ``tasks.py``). The only genuine per-domain differences live here:

- **medications** — the FDA NDC export is versioned behind the openFDA manifest
  (``api.fda.gov/download.json`` → ``results.drug.ndc.partitions[].file``), so its URL(s) are
  resolved at fetch time (and it may, in future, be split across partitions);
- **procedures / diagnoses** — the CMS ICD-10 order files sit at stable per-fiscal-year URLs.

Downloads land in a gitignored, project-local ``var/`` directory (anchored to this package, so
it is independent of the current working directory) — not the workspace root — so the transient
source files stay contained and travel with the code.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Project-local, gitignored scratch dir for downloaded source files.
VAR_DIR = Path(__file__).resolve().parent.parent / "var"

# CMS publishes the ICD-10 order files per fiscal year at stable URLs; bump for the next update.
ICD10_YEAR = 2026

# openFDA publishes a manifest listing every dataset's current export partition(s).
OPENFDA_MANIFEST_URL = "https://api.fda.gov/download.json"


def _require_https(url: str) -> str:
    """Guard every source URL to HTTPS (also satisfies bandit B310 on the urlopen calls)."""
    if not url.startswith("https://"):
        raise ValueError(f"refusing to fetch a non-HTTPS source URL: {url!r}")
    return url


def _read_json(url: str) -> Any:
    # URL forced to HTTPS by _require_https, so the file:/ftp: schemes B310 warns about can't occur.
    with urllib.request.urlopen(_require_https(url), timeout=30) as resp:  # nosec B310
        return json.load(resp)


def _openfda_ndc_urls() -> list[str]:
    """Resolve the current FDA NDC export partition URL(s) from the openFDA manifest."""
    manifest = _read_json(OPENFDA_MANIFEST_URL)
    partitions = manifest["results"]["drug"]["ndc"]["partitions"]
    return [_require_https(p["file"]) for p in partitions]


def _basename(url: str) -> str:
    return url.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class Source:
    """A reference-data source: how to resolve its download URL(s) and where its files land."""

    name: str  # domain key: "drugs" | "procedures" | "diagnoses"
    resolve_urls: Callable[[], list[str]]  # current download URL(s)
    pattern: str  # glob under var/ — the files ingest reads and clean deletes
    dest_name: Callable[[str], str]  # local filename to save a resolved URL as

    def paths(self) -> list[Path]:
        """Files currently present under var/ for this source (what ingest / clean act on)."""
        return sorted(VAR_DIR.glob(self.pattern))


SOURCES: dict[str, Source] = {
    "drugs": Source(
        name="drugs",
        resolve_urls=_openfda_ndc_urls,
        pattern="drug-ndc-*.json.zip",
        dest_name=_basename,  # e.g. drug-ndc-0001-of-0001.json.zip
    ),
    "procedures": Source(
        name="procedures",
        resolve_urls=lambda: [
            _require_https(
                "https://www.cms.gov/files/zip/"
                f"{ICD10_YEAR}-icd-10-pcs-order-file-long-and-abbreviated-titles.zip"
            )
        ],
        pattern=f"icd10pcs_order_{ICD10_YEAR}.zip",
        dest_name=lambda _url: f"icd10pcs_order_{ICD10_YEAR}.zip",
    ),
    "diagnoses": Source(
        name="diagnoses",
        resolve_urls=lambda: [
            _require_https(
                f"https://www.cms.gov/files/zip/{ICD10_YEAR}-code-descriptions-tabular-order.zip"
            )
        ],
        pattern=f"icd10cm_order_{ICD10_YEAR}.zip",
        dest_name=lambda _url: f"icd10cm_order_{ICD10_YEAR}.zip",
    ),
}


def get_source(name: str) -> Source:
    if name not in SOURCES:
        raise SystemExit(f"unknown source {name!r}; choose from {', '.join(SOURCES)}")
    return SOURCES[name]
