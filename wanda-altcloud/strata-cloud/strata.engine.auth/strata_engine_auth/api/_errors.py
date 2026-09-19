"""Cognito ClientError → HTTP status mapping — the REFRESH route's only.

Login stopped using this: its failures are deliberately
indistinguishable (one generic 401 for wrong password, unknown user, and every
migration leg), so it maps its own errors in the route. Refresh
tokens are unguessable bearer material, not enumerable identifiers, so refresh
keeps the informative mapping; a deleted user's refresh reads as an invalid
token (401), never a 404.
"""

from typing import NoReturn

from botocore.exceptions import ClientError
from fastapi import HTTPException, status

# Maps Cognito error codes -> HTTP status. Anything unmapped becomes a 400.
ERROR_STATUS = {
    "NotAuthorizedException": status.HTTP_401_UNAUTHORIZED,
    "UserNotConfirmedException": status.HTTP_403_FORBIDDEN,
    "UserNotFoundException": status.HTTP_401_UNAUTHORIZED,
    "InvalidParameterException": status.HTTP_400_BAD_REQUEST,
}


def reraise(err: ClientError) -> NoReturn:
    code = err.response["Error"]["Code"]
    message = err.response["Error"]["Message"]
    raise HTTPException(ERROR_STATUS.get(code, status.HTTP_400_BAD_REQUEST), message)
