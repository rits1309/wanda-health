"""Delete a reference-data source's downloaded files from the project-local ``var/`` directory.

Run via inv — never by hand:
    inv clean-drugs | clean-procedures | clean-diagnoses
      → python -m scripts.clean <drugs|procedures|diagnoses>

The explicit delete step of the fetch → ingest → clean lifecycle. Ingest reads from ``var/``,
so run clean only after ingest has loaded the data into Postgres.
"""

from __future__ import annotations

import argparse

from scripts.sources import get_source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="drugs | procedures | diagnoses")
    args = parser.parse_args()

    source = get_source(args.source)
    paths = source.paths()
    if not paths:
        print(f"Nothing to clean for {source.name!r} (no {source.pattern} in var/).")
        return
    for path in paths:
        path.unlink()
        print(f"Removed {path}")


if __name__ == "__main__":
    main()
