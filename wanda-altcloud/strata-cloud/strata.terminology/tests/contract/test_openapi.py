"""Contract / fuzz tests.

Schemathesis reads FastAPI's auto-generated OpenAPI schema and generates a wide range of
requests for every operation, asserting each response conforms to the declared schema
(status codes, content types, response bodies). This catches contract drift and unhandled
inputs for free — no hand-written cases — and grows automatically as routes are added.

Every case carries a valid dev bearer token (the seam guards /v1 — any authenticated user;
the 401 path is covered by ``tests/api/test_auth_guards.py``, not the fuzz run).
"""

import pytest
import schemathesis
from strata_identity.roles import ROLE_COACH
from strata_identity.security import mint_dev_token

from strata_terminology.core.config import settings
from strata_terminology.main import app

schema = schemathesis.openapi.from_asgi("/openapi.json", app)

assert settings.dev_auth_secret is not None
AUTH = {"Authorization": f"Bearer {mint_dev_token(settings.dev_auth_secret, 't-1', [ROLE_COACH])}"}


@pytest.mark.contract
@schema.parametrize()
def test_api_conforms_to_schema(case: schemathesis.Case) -> None:
    """Schemathesis fuzzes every route: responses always conform to the OpenAPI contract."""
    case.call_and_validate(headers=AUTH)
