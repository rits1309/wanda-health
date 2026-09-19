"""Edge auth seam: a static shared secret locally, an API key in Phase 2.

This guards the *provider/dispatch* edge (SmartMeter deliveries, the registration caller) —
machine-to-machine requests with no member identity. Routes acting on behalf of a *person*
must instead consume the shared verification seam from ``strata.identity``;
 Settings requires the secret at startup, so an unconfigured deployment fails
closed before it can serve.
"""

import secrets

from fastapi import Header, HTTPException, status

from app.config import settings


async def require_edge_secret(
    x_api_key: str | None = Header(default=None, description="The shared edge secret."),
) -> None:
    """Reject the request unless it carries the shared edge secret."""
    expected = settings.edge_shared_secret
    # Compare as bytes: compare_digest raises TypeError on non-ASCII str, and header values
    # can carry any latin-1 byte — a crafted key must yield 401, not a 500.
    if (
        not expected
        or x_api_key is None
        or not secrets.compare_digest(x_api_key.encode(), expected.encode())
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key."
        )
