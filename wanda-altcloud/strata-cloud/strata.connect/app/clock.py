"""Injectable UTC clock — the single seam for reading time (booking's core/clock.py shape).

All services read time through :data:`clock` so tests can pin it deterministically
(monkeypatch ``clock.now``); timestamps stay UTC everywhere.
"""

from datetime import UTC, datetime


class Clock:
    def now(self) -> datetime:
        """Current time, timezone-aware UTC."""
        return datetime.now(UTC)


clock = Clock()
