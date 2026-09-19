"""The domains aggregator imports every domain package.

The ownership gate, the field-classification completeness gate, and the
Alembic drift check all key off ``Base.metadata.tables``, which is only fully
populated when ``strata_core/domains/__init__.py`` imports **every** domain
subpackage. That import list is hand-maintained, so a new domain package added
on disk but not wired into the aggregator would register no tables — and every
downstream gate would pass blind. This guard closes that hole: a domain
package on disk that the aggregator does not import fails the build.
"""

import subprocess
import sys

# pkgutil.iter_modules with the ispkg filter reports only real subpackages
# (those with an __init__), so a bare __pycache__ or a stale directory left by
# a branch switch is ignored — the discovery cannot be fooled by leftovers.
from pkgutil import iter_modules

import strata_core.domains as domains
from strata_core.domains import __path__ as domains_path

FIX_HINT = "wire it into strata_core/domains/__init__.py (import + __all__)"


def _domain_packages_on_disk() -> set[str]:
    return {name for _, name, is_pkg in iter_modules(domains_path) if is_pkg}


def test_all_matches_disk_packages() -> None:
    """``__all__`` names exactly the domain packages present on disk."""
    on_disk = _domain_packages_on_disk()
    declared = set(domains.__all__)
    assert declared == on_disk, (
        f"strata_core/domains/__init__.py __all__ {sorted(declared)} != "
        f"packages on disk {sorted(on_disk)} — {FIX_HINT}"
    )


def test_aggregator_actually_imports_every_domain_package() -> None:
    """Importing the aggregator registers each domain package's models.

    Verified in a clean subprocess: importing a submodule elsewhere in the
    suite would bind it on the parent package, so an in-process attribute
    check could pass falsely. A fresh interpreter imports only what the
    aggregator's ``__init__`` reaches.
    """
    on_disk = _domain_packages_on_disk()
    code = (
        "import strata_core.domains, sys\n"
        "print(' '.join(sorted(m for m in sys.modules "
        "if m.startswith('strata_core.domains.'))))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    imported = {module.split(".")[2] for module in result.stdout.split() if module.count(".") >= 2}
    missing = on_disk - imported
    assert not missing, (
        f"domain package(s) on disk not imported by the aggregator: {sorted(missing)} — {FIX_HINT}"
    )
