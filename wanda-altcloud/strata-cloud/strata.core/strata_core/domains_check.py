"""Dev helper: list domain packages on disk not wired into the aggregator.

The aggregator's import list (`strata_core/domains/__init__.py`) is explicit on
purpose (IDE- and type-checker-friendly). Keeping it complete is Claude's job,
not a human's: run this (``inv domains-check``) during any schema work to see
any domain package present on disk but not yet imported, then wire it in. The
guard test (`tests/test_domains.py`) is the CI backstop that makes a lapse a
red build rather than a silent gate bypass.
"""

import sys
from pkgutil import iter_modules

import strata_core.domains as domains

AGGREGATOR = "strata_core/domains/__init__.py"


def missing_packages() -> list[str]:
    """Domain packages on disk (real packages only) absent from the aggregator's ``__all__``."""
    on_disk = {name for _, name, is_pkg in iter_modules(domains.__path__) if is_pkg}
    return sorted(on_disk - set(domains.__all__))


def main() -> int:
    missing = missing_packages()
    if missing:
        print(f"Domain packages on disk NOT wired into {AGGREGATOR}:")
        for name in missing:
            print(f"  - {name}  (add to the import statement and __all__)")
        print("Wire each in, then re-run. The guard test enforces this in CI.")
        return 1
    print(f"All {len(domains.__all__)} domain packages are wired into the aggregator.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
