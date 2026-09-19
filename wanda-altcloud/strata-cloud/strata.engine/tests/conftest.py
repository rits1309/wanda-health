"""Test fixtures. Tracing export is off by default (STRATA_OTEL_TRACES_EXPORTER=none)."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from strata_engine.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield TestClient(create_app())
