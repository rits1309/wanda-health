"""Thin wrapper over the Cognito Identity Provider API — the seam tests fake.

Two clients, one per trust level. The runtime operations (InitiateAuth for
login and refresh) are *unauthenticated* Cognito actions, so that client is
configured with UNSIGNED requests and needs no AWS credentials; security comes
from the optional app-client secret. Admin operations go through
``admin_cognito`` — the CREDENTIALED client (Identity): the dev seed today,
the migration login's AdminCreateUser/AdminSetUserPassword/AdminDeleteUser at
Identity. SignUp/ConfirmSignUp/GetUser are gone with self-registration and
lazy provisioning (, Identity).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import threading
from typing import Any

import boto3
from botocore import UNSIGNED
from botocore.config import Config

from strata_engine_auth.core.config import settings

_client: Any = None
_admin_client: Any = None
_client_lock = threading.Lock()


def _cognito() -> Any:
    """The boto3 client, created lazily (locked: sync routes run in a threadpool,
    and concurrent first requests must not race botocore's non-thread-safe
    default-session initialisation)."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = boto3.client(
                    "cognito-idp",
                    region_name=settings.cognito_region,
                    config=Config(signature_version=UNSIGNED),
                )
    return _client


def admin_cognito() -> Any:
    """The CREDENTIALED client — Cognito admin operations only.

    Consumers: the dev seed (``scripts/seed.py``) today; the migration login's
    AdminCreateUser/AdminSetUserPassword/AdminDeleteUser at Identity.
    Requests are signed with credentials from the standard AWS chain, never
    from Settings: a developer session locally (``aws login``;
    ``STRATA_AWS_PROFILE`` selects a named profile), an IAM role scoped to
    exactly those three admin actions when deployed.
    """
    global _admin_client
    if _admin_client is None:
        with _client_lock:
            if _admin_client is None:
                _admin_client = boto3.Session(profile_name=settings.aws_profile).client(
                    "cognito-idp", region_name=settings.cognito_region
                )
    return _admin_client


def admin_create_user(username: str, email: str | None) -> str:
    """AdminCreateUser with messaging suppressed; returns the new user's sub.

    The email (when present) is set VERIFIED — that is what makes the pool's
    alias resolve it at sign-in. The username must never be
    email-format (an alias pool rejects those): the migration branch passes the
    typed app username, or the profile id when the identifier was an email.
    """
    attributes = []
    if email is not None:
        attributes = [
            {"Name": "email", "Value": email},
            {"Name": "email_verified", "Value": "true"},
        ]
    response = admin_cognito().admin_create_user(
        UserPoolId=settings.cognito_user_pool_id,
        Username=username,
        MessageAction="SUPPRESS",
        UserAttributes=attributes,
    )
    found = {a["Name"]: a["Value"] for a in response["User"]["Attributes"]}
    return str(found["sub"])


def admin_set_permanent_password(username: str, password: str) -> None:
    """Set (or reset) the password permanently — the user lands CONFIRMED."""
    admin_cognito().admin_set_user_password(
        UserPoolId=settings.cognito_user_pool_id,
        Username=username,
        Password=password,
        Permanent=True,
    )


def admin_add_user_to_group(username: str, group: str) -> None:
    """Mirror an imported role onto the pool (coarse roles ride tokens as
    ``cognito:groups``) — the migration flow's half of what the seed's
    group converger does. Found missing at the live walkthrough: without it
    a migrated user's tokens carry no roles at all."""
    admin_cognito().admin_add_user_to_group(
        UserPoolId=settings.cognito_user_pool_id, Username=username, GroupName=group
    )


def admin_delete_user(username: str) -> None:
    """The compensation: remove a just-created user after a failed adoption."""
    admin_cognito().admin_delete_user(UserPoolId=settings.cognito_user_pool_id, Username=username)


def _secret_hash(username: str) -> str | None:
    """Cognito requires HMAC-SHA256(username + client_id) when the client has a secret."""
    if not settings.cognito_client_secret:
        return None
    message = (username + settings.cognito_client_id).encode()
    digest = hmac.new(settings.cognito_client_secret.encode(), message, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def login(identifier: str, password: str) -> dict[str, Any]:
    """USER_PASSWORD_AUTH with a username OR an email in the same field —
    the pool's email alias resolves emails to the account."""
    auth_params = {"USERNAME": identifier, "PASSWORD": password}
    if (sh := _secret_hash(identifier)) is not None:
        auth_params["SECRET_HASH"] = sh
    result: dict[str, Any] = _cognito().initiate_auth(
        ClientId=settings.cognito_client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters=auth_params,
    )
    return result


def refresh(refresh_token: str, sub: str | None = None) -> dict[str, Any]:
    # REFRESH_TOKEN_AUTH's SECRET_HASH is keyed on the token's `sub` claim —
    # NOT the identifier used at sign-in (since the pool recreation the
    # username is the legacy app username and the email an alias; neither keys
    # this hash). Only needed when the app client has a secret. Returns new
    # access/id tokens; Cognito does NOT issue a new refresh token in this flow.
    auth_params = {"REFRESH_TOKEN": refresh_token}
    if settings.cognito_client_secret:
        if not sub:
            # Backstop only — the route validates and answers 400 before calling
            # here (HTTP mapping stays in the API layer). Reaching this is a
            # programming error, never a user-input path.
            raise RuntimeError(
                "refresh with a confidential client requires sub"
                " (the token's subject — the real Cognito username)"
            )
        if (sh := _secret_hash(sub)) is not None:
            auth_params["SECRET_HASH"] = sh
    result: dict[str, Any] = _cognito().initiate_auth(
        ClientId=settings.cognito_client_id,
        AuthFlow="REFRESH_TOKEN_AUTH",
        AuthParameters=auth_params,
    )
    return result
