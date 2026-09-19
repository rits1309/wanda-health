"""The clinical write seam for member demographics.

Every helper here is keyed on ``member_user_id`` (the kernel FK — /
naming convention), takes the caller's ``AsyncSession``, and **flushes but never
commits** (the caller owns the transaction boundary). It NEVER logs a field
value: the clinical domain is PHI, so a caller gets field names at most, never
the data.

Iteration 1 covers demographics; the diagnoses / procedures /
medications converge helpers arrive in iteration 2.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession
from strata_core.domains.clinical import MemberDemographics

__all__ = ["converge_demographics"]


async def converge_demographics(
    session: AsyncSession,
    *,
    member_user_id: str,
    sex: str | None = None,
    date_of_birth: date | None = None,
    ethnicity: str | None = None,
    marital_status: str | None = None,
    height_in: Decimal | None = None,
) -> MemberDemographics:
    """Create or converge the one-to-one demographics row for ``member_user_id``.

    Idempotent: the row's primary key IS ``member_user_id``, so re-running with
    the same user updates the existing row in place rather than inserting a
    duplicate. Values land **permissively as recorded** (iteration 1 /
    the legacy sex / ethnicity / single-letter marital-status vocabularies are
    not mapped or validated here). The ``user_profiles`` foreign key is enforced
    by the database, so an unknown ``member_user_id`` fails at flush rather than being
    written as an orphan. Flushes; the caller commits.

    **Full-record contract:** this writes every field from its arguments, so an
    omitted field is set to NULL — callers pass the COMPLETE demographics record
    (the import does, sourcing all fields from one legacy row). A
    partial-field-update variant will be added if and when a caller genuinely needs
    one (iteration 2); until then, do not call this to "patch" a single field.
    """
    if not member_user_id or not member_user_id.strip():
        raise ValueError("member_user_id is required to write member demographics")
    row = await session.get(MemberDemographics, member_user_id)
    if row is None:
        row = MemberDemographics(member_user_id=member_user_id)
        session.add(row)
    row.sex = sex
    row.date_of_birth = date_of_birth
    row.ethnicity = ethnicity
    row.marital_status = marital_status
    row.height_in = height_in
    await session.flush()
    return row
