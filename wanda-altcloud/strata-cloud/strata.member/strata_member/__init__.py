"""strata.member — the clinical write-seam library.

The write path for the ``clinical`` domain: session-taking helpers
that own the domain's write invariants so no caller constructs clinical rows by
hand. The models and migrations live in ``strata-core`` (``strata_core.domains.clinical``);
this package imports them like every other consumer and adds only the write logic.

A **library, not a service** — no FastAPI app. Consumers pass their own
``AsyncSession`` and own the commit: the future member-profile service (
canonical name) and, until it ships, the migration import.
"""

from strata_member.demographics import converge_demographics

__all__ = ["converge_demographics"]
