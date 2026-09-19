"""Contract / fuzz tests (sibling convention — see strata.engine).

Schemathesis reads FastAPI's auto-generated OpenAPI schema and generates a wide
range of requests for every operation, asserting each response conforms to the
declared contract. The Cognito seam is faked by the autouse conftest fixture, so
fuzzing never reaches AWS.
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
import schemathesis

from strata_engine_auth.db.session import get_session
from strata_engine_auth.main import app


async def _no_session() -> AsyncIterator[None]:
    # Schemathesis drives the ASGI app without running the lifespan, so the real
    # dependency would 500 on an uninitialised engine. Auth rejects (401) before
    # any session use; sign-up's profile write defers (its documented outage path).
    yield None


app.dependency_overrides[get_session] = _no_session

schema = schemathesis.openapi.from_asgi("/openapi.json", app)


@pytest.mark.contract
@schema.parametrize()
def test_api_conforms_to_schema(case: "schemathesis.Case[Any]") -> None:
    """Schemathesis fuzzes every route: responses always conform to the OpenAPI contract."""
    case.call_and_validate()
