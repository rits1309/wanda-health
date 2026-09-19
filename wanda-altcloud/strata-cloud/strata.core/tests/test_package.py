"""Packaging + settings smoke tests — the stub consumer.

These prove the mechanism every consumer relies on (the local
path-dependency pattern): the distribution installs, imports, exposes typed
settings read from the ``STRATA_`` environment, and ships its ``py.typed``
marker so consumers' strict type-checkers see through it.
"""

import importlib.metadata
import importlib.resources

import pytest
from pydantic import ValidationError

from strata_core.settings import Settings


def test_distribution_is_installed() -> None:
    """The strata-core distribution is installed at the expected version."""
    assert importlib.metadata.version("strata-core") == "0.1.0"


def test_py_typed_marker_ships() -> None:
    """The py.typed marker ships, so consumers' type-checkers trust the package's types."""
    assert importlib.resources.files("strata_core").joinpath("py.typed").is_file()


def test_settings_read_the_strata_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings read the database URL from STRATA_DATABASE_URL."""
    url = "postgresql+asyncpg://u:p@localhost:5432/strata"
    monkeypatch.setenv("STRATA_DATABASE_URL", url)
    assert Settings().database_url == url


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings without STRATA_DATABASE_URL fail validation; the URL is mandatory."""
    monkeypatch.delenv("STRATA_DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings()
