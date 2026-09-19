"""Download a reference-data source into the gitignored, project-local ``var/`` directory.

Run via inv — never by hand:
    inv fetch-drugs | fetch-procedures | fetch-diagnoses
      → python -m scripts.fetch <drugs|procedures|diagnoses>

Resolves the current download URL(s) from ``scripts.sources`` (the openFDA manifest for
medications; the stable CMS per-year URL for procedures / diagnoses), streams each to ``var/``,
and prints the saved path. Ingest and delete are deliberately separate steps
(``scripts.ingest_*`` / ``scripts.clean``); ``inv refresh-<domain>`` chains all three.
"""

from __future__ import annotations

import argparse
import shutil
import urllib.request
from pathlib import Path

from scripts.sources import VAR_DIR, get_source


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    # URL is forced to HTTPS in scripts.sources (resolve_urls → _require_https) before we get here.
    with urllib.request.urlopen(url, timeout=60) as resp, dest.open("wb") as fh:  # nosec B310
        shutil.copyfileobj(resp, fh)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="drugs | procedures | diagnoses")
    args = parser.parse_args()

    source = get_source(args.source)
    for url in source.resolve_urls():
        dest = VAR_DIR / source.dest_name(url)
        print(f"Downloading {url}\n         → {dest}")
        _download(url, dest)
        print(f"  saved {dest.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
