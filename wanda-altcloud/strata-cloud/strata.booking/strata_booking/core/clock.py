"""Injectable UTC clock — the single seam for reading time.

All services read time through :data:`clock` so tests can pin it deterministically
(monkeypatch ``clock.now``) — slot generation, cancellation windows, token expiry, and
no-show detection all depend on "now".
"""

from datetime import UTC, datetime


class Clock:
    def now(self) -> datetime:
        """Current time, timezone-aware UTC."""
        return datetime.now(UTC)


clock = Clock()
