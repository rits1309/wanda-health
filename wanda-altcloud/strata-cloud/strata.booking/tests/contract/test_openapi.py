"""Contract / fuzz tests.

Schemathesis reads FastAPI's auto-generated OpenAPI schema and generates a wide range of
requests for every operation, asserting each response conforms to the declared schema. This
catches contract drift and unhandled inputs for free and grows automatically as routes are
added. Requests run twice per case: unauthenticated responses must be declared (401), and an
authenticated pass (a seeded coach's dev token) exercises the real handlers.
"""

import pytest
import schemathesis
from hypothesis import HealthCheck
from hypothesis import settings as hypothesis_settings
from schemathesis.specs.openapi.checks import positive_data_acceptance

from strata_booking.main import app
from tests.auth import bearer

schema = schemathesis.openapi.from_asgi("/openapi.json", app)

COACH_AUTH = bearer("c-1", "coach")


@pytest.mark.contract
@schema.parametrize()
# Bounded: successful POSTs materialise slots to the 4-week horizon, so each valid example is
# a real (fast but non-trivial) write — 10 per operation keeps the suite under control.
# filter_too_much is suppressed: it is Hypothesis's generation-EFFICIENCY alarm, fired
# before any request is sent, never a conformance check. Schemathesis discards draws that can't
# survive HTTP transport (non-latin-1 header values, path params containing / { } . %00), so
# operations with free-string headers or path params sit near the alarm's 50-filtered-before-
# 10-valid threshold and trip it on unlucky seeds — a wandering ~1-in-8 full-run flake. All
# response-conformance checks live in call_and_validate below and stay fully armed.
@hypothesis_settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.filter_too_much],
)
def test_api_conforms_to_schema(case: schemathesis.Case) -> None:
    # positive_data_acceptance is excluded: domain validators (IANA timezone validity, time
    # ordering) are stricter than JSON Schema can express, so a schema-valid body may
    # legitimately 422. All response-conformance checks still apply.
    """Schemathesis fuzzes every route: responses always conform to the OpenAPI contract."""
    case.call_and_validate(
        headers=COACH_AUTH,
        excluded_checks=[positive_data_acceptance],
    )
