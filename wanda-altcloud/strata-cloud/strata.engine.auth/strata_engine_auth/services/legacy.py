"""The legacy credentials seam — the migration login's one legacy touchpoint.

``LegacyCredentialsClient`` is the protocol the migration branch consumes and
tests stub; ``HttpLegacyCredentialsClient`` targets the legacy platform's
credentials-check endpoint (POST ``{username, password}``). Owner-relayed
contract (21-07-2026):

- patient accepted: ``{"success": true, "django_user_id": N, "internal_patient_id": M}``
- coach accepted:   ``{"success": true, "django_user_id": N, "internal_client_user_id": M}``
- rejected:         ``{"success": false, "error": "Invalid credentials"}`` — generic
  by design (the platform itself does not distinguish unknown user from wrong
  password)

Parsing keys on ``success`` regardless of HTTP status and tolerates unknown extra
fields; ``django_user_id`` is the ``Legacy Summit Django User ID`` mapping key,
and the ``internal_*`` id doubles as the user-kind discriminator AND the
``Legacy Summit Patient/Coach ID`` cross-check value. UAT requires no request
authentication; production will require an API key — ``STRATA_LEGACY_API_KEY``
is attached when set (header name provisional until the production details land).
Transport failures and malformed bodies raise ``LegacyUnavailableError`` — the
caller fails the login with the same generic error as every other leg and
never logs credentials, identifiers, or response bodies.
"""

from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx

from strata_engine_auth.core.config import Settings

# Provisional until the production endpoint's details land ( comment
# trail); UAT sends no key at all, so nothing depends on this name yet.
API_KEY_HEADER = "X-API-Key"


class LegacyUnavailableError(RuntimeError):
    """The endpoint could not give a verdict (transport failure/malformed body).

    Deliberately message-poor: raised on paths carrying credentials, so no
    request or response content may ride on it.
    """


@dataclass(frozen=True)
class LegacyVerdict:
    """The outcome of a credentials check, contract-shaped but transport-free."""

    accepted: bool
    #: The platform user id (stringified ``django_user_id``) — the value imported
    #: ``Legacy Summit Django User ID`` mappings hold. Present exactly
    #: when accepted.
    legacy_user_id: str | None = None
    #: Which ``internal_*`` id the response carried — the defence-in-depth
    #: user-kind cross-check against the imported profile's roles.
    kind: Literal["patient", "coach"] | None = None
    #: The stringified ``internal_patient_id``/``internal_client_user_id`` value —
    #: what the imported ``Legacy Summit Patient/Coach ID`` mapping must hold
    #: Present exactly when ``kind`` is.
    internal_id: str | None = None


REJECTED = LegacyVerdict(accepted=False)


class LegacyCredentialsClient(Protocol):
    async def check(self, username: str, password: str) -> LegacyVerdict:
        """Validate credentials against the legacy platform; never raises on a
        clean rejection — only on failure to obtain a verdict at all."""
        ...


def _parse_verdict(body: Any) -> LegacyVerdict:
    if not isinstance(body, dict) or "success" not in body:
        raise LegacyUnavailableError("legacy response did not carry a verdict")
    if not body["success"]:
        return REJECTED
    legacy_user_id = body.get("django_user_id")
    if legacy_user_id is None:
        # Accepted without the mapping key is uninterpretable — treat as no
        # verdict rather than inventing an acceptance the gate cannot use.
        raise LegacyUnavailableError("legacy acceptance carried no django_user_id")
    kind: Literal["patient", "coach"] | None = None
    internal_id: str | None = None
    if "internal_patient_id" in body:
        kind, internal_id = "patient", str(body["internal_patient_id"])
    elif "internal_client_user_id" in body:
        kind, internal_id = "coach", str(body["internal_client_user_id"])
    return LegacyVerdict(
        accepted=True, legacy_user_id=str(legacy_user_id), kind=kind, internal_id=internal_id
    )


class HttpLegacyCredentialsClient:
    def __init__(
        self,
        *,
        check_url: str,
        api_key: str | None = None,
        timeout_seconds: float = 3.0,
        transport: httpx.AsyncBaseTransport | None = None,  # tests inject a MockTransport
    ) -> None:
        self._check_url = check_url
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._transport = transport

    async def check(self, username: str, password: str) -> LegacyVerdict:
        headers = {API_KEY_HEADER: self._api_key} if self._api_key else {}
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(
                    self._check_url,
                    json={"username": username, "password": password},
                    headers=headers,
                )
            return _parse_verdict(response.json())
        except LegacyUnavailableError:
            raise
        except Exception as err:  # timeouts, connection errors, non-JSON bodies
            # No err message pass-through: it can echo the request.
            raise LegacyUnavailableError(
                f"legacy credentials check failed ({type(err).__name__})"
            ) from None


def create_legacy_client(settings: Settings) -> LegacyCredentialsClient | None:
    """The configured client, or None when no base URL is set — the migration
    branch is then disabled and unknown users simply fail generically."""
    if not settings.legacy_api_base_url:
        return None
    return HttpLegacyCredentialsClient(
        check_url=settings.legacy_api_base_url.rstrip("/") + settings.legacy_check_path,
        api_key=settings.legacy_api_key,
        timeout_seconds=settings.legacy_timeout_seconds,
    )
