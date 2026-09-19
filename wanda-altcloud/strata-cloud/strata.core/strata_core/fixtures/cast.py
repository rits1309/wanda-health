"""The development cast — the database half, defined once.

Ported from ``strata.engine.auth``'s identity seed (which mirrors booking's
demo cast): 3 coaches with mixed languages/timezones, 1 admin, 4 members —
Dana holds coach **and** admin, the multi-role proof. From the auth seed
delegates its database rows to this cast (via the ``demo`` profile) and keeps
only the Cognito half, so the two halves cannot drift.

Login-capable cast members (those with an email) get an active ``Cognito Sub``
mapping row (``kernel.identifiers``) holding the deterministic
placeholder ``seed-sub-<username>``; the auth seed converges profiles by email
and supersedes the placeholder mapping with the real pool subject (ending it,
never deleting — pool-recreated subjects re-adopt the same way). An email-less
member (Sam Nguyen) gets NO mapping and no login: the means-test member
— bookable end to end with neither.

``profile_id`` is the deterministic ``user_profiles.id``: the values are
booking's historical walkthrough ids (``c-1``/``m-1``/``a-1``…), kept so the
dev-token seam, the Bruno collection and the booking suite keep working
verbatim across the swap. The prefix is historical, not semantic — Dana
(``c-2``) also holds Admin.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CastMember:
    email: str | None
    username: str
    first_name: str
    last_name: str
    roles: tuple[str, ...]
    timezone: str
    languages: tuple[str, ...]
    preferred_language: str | None = None  # None where never chosen (person-level)
    # Optional presentation override: the cast deliberately
    # mixes set and unset so seeded data exercises both rendering paths.
    display_name: str | None = field(kw_only=True, default=None)
    profile_id: str = field(kw_only=True, default="")
    # Synthetic Legacy Summit identifiers: set only on the
    # post-migrated stand-in, so a clean seed carries the mappings an import
    # leaves behind without a real import. Reserved fake range — never real ids.
    legacy_django_id: str | None = field(kw_only=True, default=None)
    legacy_patient_id: str | None = field(kw_only=True, default=None)

    @property
    def placeholder_sub(self) -> str | None:
        """The seeded ``Cognito Sub`` mapping value — None for a no-login member."""
        return f"seed-sub-{self.username}" if self.email is not None else None

    @property
    def effective_display_name(self) -> str:
        """What this person renders as: the override, else "First Last"."""
        return self.display_name or f"{self.first_name} {self.last_name}"


DEMO_CAST: tuple[CastMember, ...] = (
    CastMember(
        "seed-casey@wandahealth.com",
        "casey",
        "Casey",
        "Ellis",
        ("Coach",),
        "America/New_York",
        ("en",),
        profile_id="c-1",
    ),
    CastMember(
        "seed-dana@wandahealth.com",
        "dana",
        "Dana",
        "Reyes",
        ("Coach", "Admin"),
        "America/Chicago",
        ("en", "es"),
        display_name="Dana",  # the worked display-name override example
        profile_id="c-2",
    ),
    CastMember(
        "seed-elena@wandahealth.com",
        "elena",
        "Elena",
        "Marti",
        ("Coach",),
        "America/Los_Angeles",
        ("es",),
        profile_id="c-3",
    ),
    CastMember(
        "seed-alex@wandahealth.com",
        "alex",
        "Alex",
        "Morgan",
        ("Admin",),
        "America/New_York",
        ("en",),
        profile_id="a-1",
    ),
    CastMember(
        "seed-morgan@wandahealth.com",
        "morgan",
        "Morgan",
        "Lee",
        ("Member",),
        "America/New_York",
        ("en",),
        "en",
        profile_id="m-1",
    ),
    CastMember(
        "seed-priya@wandahealth.com",
        "priya",
        "Priya",
        "Patel",
        ("Member",),
        "America/Chicago",
        ("en",),
        "en",
        profile_id="m-5",
    ),
    CastMember(
        "seed-luis@wandahealth.com",
        "luis",
        "Luis",
        "Ortega",
        ("Member",),
        "America/Denver",
        ("es",),
        "es",
        profile_id="m-4",
    ),
    CastMember(
        "seed-sam@wandahealth.com",
        "sam",
        "Sam",
        "Carter",
        ("Member",),
        "Europe/London",
        ("en", "es"),
        "es",
        display_name="Sam",  # a member-side override, so both roles exercise it
        profile_id="m-2",
    ),
    # A POST-MIGRATED ex-legacy patient: the fixture stand-in for the
    # UAT imports AFTER their migration login completed. Login-capable like the
    # rest of the demo cast (the auth seed creates her pool account from the
    # email) and carrying the Legacy Summit mappings an import leaves behind
    # (reserved fake ids — never real-person data), so the booking-acceptance
    # converge assigns her into p-1 and she appears in real pickers from a
    # clean seed. She does NOT exercise the migration PROCESS itself
    # (that needs a real legacy-backed user or a mock legacy service).
    CastMember(
        "seed-marta@wandahealth.com",
        "marta",
        "Marta",
        "Iglesias",
        ("Member",),
        "America/New_York",
        ("es", "en"),
        "es",
        profile_id="m-6",
        legacy_django_id="9000001",
        legacy_patient_id="9100001",
    ),
)


#: Booking-walkthrough-only extras (loaded by the ``booking-acceptance`` profile,
#: not by ``demo``). They have no Cognito seed user: they exist as booking DATA
#: (someone to book appointments for). Sam Nguyen is the means-test
#: member: **no email, no login, no
#: Cognito mapping** — and bookable end to end exactly like everyone else.
BOOKING_EXTRAS: tuple[CastMember, ...] = (
    CastMember(
        None,
        "nguyen",
        "Sam",
        "Nguyen",
        ("Member",),
        "America/Los_Angeles",
        ("en",),
        "en",
        profile_id="m-3",
    ),
)
