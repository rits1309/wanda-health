"""Run the canonical migrations — the consumer test-bootstrap seam.

Consumers build databases (above all ephemeral test databases) by replaying the
canonical history, never by ``create_all``. The Alembic environment lives in the
strata.core project root next to this package, which the local path-dependency
install resolves; packaging the environment into a wheel is
a later concern.

The alembic env drives its own ``asyncio.run``, so call these from sync code
(e.g. a sync pytest fixture), never from inside a running event loop.
"""

from pathlib import Path

from alembic.config import Config

from alembic import command

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def alembic_config(database_url: str) -> Config:
    """A Config whose URL wins over the STRATA_DATABASE_URL env var.

    The URL travels via ``Config.attributes`` — the supported programmatic
    channel, which alembic/env.py consults FIRST — so a consumer suite can point
    at its ephemeral container while CI exports a placeholder URL for the app's
    own settings, with no process-global environment mutation.
    """
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    # `%` escaped for the ini layer's interpolating ConfigParser (URL-encoded
    # credentials otherwise raise); attributes carries the raw URL — env.py reads
    # that first and re-escapes for its own ini write.
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    cfg.attributes["sqlalchemy_url"] = database_url
    return cfg


def upgrade_to_head(database_url: str) -> None:
    """Apply the full canonical history (schema + reference rows, e.g. the role catalogue)."""
    command.upgrade(alembic_config(database_url), "head")


def downgrade_to_base(database_url: str) -> None:
    """Tear the canonical schema back down (test teardown)."""
    command.downgrade(alembic_config(database_url), "base")
