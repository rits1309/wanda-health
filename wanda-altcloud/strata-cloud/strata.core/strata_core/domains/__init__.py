"""The canonical models, organised by domain module.

Importing this package registers **every** canonical table on
``Base.metadata`` — Alembic's autogenerate target and the drift check rely on
that, so import the package, not individual modules. One writing service per
domain: see ``strata_core.ownership`` / ``OWNERSHIP.md``.

The import list below is explicit on purpose (IDE- and type-checker-friendly).
It is kept complete by Claude, not by a human remembering: when a new domain
package is added, wire it into the import statement and ``__all__``
(``inv domains-check`` lists any package on disk that is missing). The guard
test ``tests/test_domains.py`` is the CI backstop, so an unwired package is a
red build, never a silent gate bypass, whoever edits this file.
"""

from strata_core.domains import booking, clinical, device_readings, kernel, reference

__all__ = ["booking", "clinical", "device_readings", "kernel", "reference"]
