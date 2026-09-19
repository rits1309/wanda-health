"""The profile timezone closed list is a pinned, closed constant.

The set is deliberately code, not a table (a timezones table was rejected in
planning). This guard pins it to the offered list so a silent drift
(a zone dropped, a non-offered zone added, the creation default falling outside
the list) fails the build rather than reaching a profile write.
"""

from strata_core.domains.kernel import DEFAULT_PROFILE_TIMEZONE, PROFILE_TIMEZONES


def test_timezone_list_is_the_eight_offered_zones() -> None:
    """The closed list is exactly the offered zones (seven US plus UK), no duplicates."""
    assert PROFILE_TIMEZONES == (
        "America/New_York",
        "America/Chicago",
        "America/Denver",
        "America/Phoenix",
        "America/Los_Angeles",
        "America/Anchorage",
        "Pacific/Honolulu",
        "Europe/London",
    )
    assert len(set(PROFILE_TIMEZONES)) == len(PROFILE_TIMEZONES)  # no duplicates


def test_creation_default_is_within_the_closed_list() -> None:
    """No new row is ever outside the offered list."""
    assert DEFAULT_PROFILE_TIMEZONE == "America/New_York"
    assert DEFAULT_PROFILE_TIMEZONE in PROFILE_TIMEZONES
