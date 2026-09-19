"""Guard: foreign keys to ``user_profiles.id`` follow the naming convention.

The platform convention: a foreign key to
``user_profiles.id`` is named ``<relationship>_user_id`` — the ``_user_id`` suffix
marks the value a user-profile id; the prefix marks the relationship the user holds
in the row (``member_user_id`` for a member's clinical facts, ``coach_user_id`` for a
coach). Bare ``user_id`` is allowed when the row is fundamentally about that one user
(``user_roles``, ``user_languages``, ``user_external_ids``, device readings).

A doc can be ignored; this test fails the build, so the convention actually holds for
every new FK. Verified: with the whole schema loaded, the ONLY columns that break it
are booking's — every kernel / clinical / device-readings FK already complies.

**Grandfathered:** the ``booking`` domain predates the convention — its real
``coach_id`` / ``member_id`` / ``from_coach_id`` / ``to_coach_id`` FKs to
``user_profiles.id`` (on ``appointments``, ``slots``, ``availability_patterns``,
``cancellation_policies``, ``unavailability_*``, ``reassignment_events``) are the
booking team's own rename effort (``coach_id`` → ``coach_user_id`` etc.). The whole
domain is exempt until then; remove it from the set below as those columns are
renamed, and the guard tightens automatically. Every OTHER domain must comply now.
"""

from strata_core.db.base import Base
from strata_core.ownership import TABLE_DOMAINS

#: Domains whose user_profiles FKs are not yet renamed to the convention (owner-tracked;
#: the booking team owns booking's rename). The set shrinks to empty as they are renamed.
_GRANDFATHERED_DOMAINS = {"booking"}


def _misnamed_user_profile_fks() -> list[str]:
    """Columns with a DDL FK to user_profiles.id whose name breaks the convention,
    outside the grandfathered domains."""
    violations: list[str] = []
    for table in Base.metadata.sorted_tables:
        if TABLE_DOMAINS.get(table.name) in _GRANDFATHERED_DOMAINS:
            continue
        for column in table.columns:
            for fk in column.foreign_keys:
                target = fk.column
                if (
                    target.table.name == "user_profiles"
                    and target.name == "id"
                    and column.name != "user_id"
                    and not column.name.endswith("_user_id")
                ):
                    violations.append(f"{table.name}.{column.name}")
    return violations


def test_user_profile_fks_follow_the_relationship_user_id_convention() -> None:
    """Every DDL FK to user_profiles.id is named `user_id`
    or `<relationship>_user_id`, outside the grandfathered booking domain."""
    violations = _misnamed_user_profile_fks()
    assert not violations, (
        "a foreign key to user_profiles.id must be named 'user_id' or "
        f"'<relationship>_user_id': {sorted(violations)}"
    )
