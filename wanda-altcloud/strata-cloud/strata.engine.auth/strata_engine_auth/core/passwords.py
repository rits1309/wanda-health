"""The pool password policy, checked locally (Identity).

Pure functions, no I/O — importable from schemas, the migration flow, and the
routes alike. The policy values live on ``Settings`` and MUST mirror the real
pool (the pool's own ``InvalidPasswordException`` is the drift backstop). The
unmet-requirement labels describe a live credential, so they may ride an HTTP
response the password's owner is reading — never a log line.
"""

import string

from strata_engine_auth.core.config import Settings

# Cognito's documented password symbol set. Space is a special character, but ONLY
# internally: Cognito refuses a leading/trailing space outright (the `whitespace`
# label below), so an edge space never counts toward the symbol class.
COGNITO_SYMBOLS = "^$*.[]{}()?-\"!@#%&/\\,><':;|_~`+= "
# Cognito's hard ceiling — checked locally so an over-long legacy password gets
# the upgrade signal instead of tripping the drift backstop.
COGNITO_MAX_LENGTH = 256


def validate_password_policy(password: str, settings: Settings) -> list[str]:
    """The unmet requirement labels for ``password``; ``[]`` means compliant.

    Labels: ``min_length``, ``max_length``, ``whitespace``, ``uppercase``,
    ``lowercase``, ``digit``, ``symbol`` — stable identifiers, not display copy.

    The character-class checks are ASCII-only (``string.ascii_uppercase`` etc.):
    Cognito's classes are A-Z / a-z / 0-9, so a non-ASCII uppercase letter or an
    Arabic-Indic digit must NOT satisfy a class — otherwise the mirror passes a
    password the pool rejects, turning the upgrade into a drift-400 dead-end.
    """
    unmet: list[str] = []
    if len(password) < settings.password_min_length:
        unmet.append("min_length")
    if len(password) > COGNITO_MAX_LENGTH:
        unmet.append("max_length")
    # Cognito rejects a leading/trailing space unconditionally, whatever the
    # class requirements — so must the mirror (review finding).
    if password[:1].isspace() or password[-1:].isspace():
        unmet.append("whitespace")
    if settings.password_require_uppercase and not any(
        c in string.ascii_uppercase for c in password
    ):
        unmet.append("uppercase")
    if settings.password_require_lowercase and not any(
        c in string.ascii_lowercase for c in password
    ):
        unmet.append("lowercase")
    if settings.password_require_digit and not any(c in string.digits for c in password):
        unmet.append("digit")
    if settings.password_require_symbol and not any(c in COGNITO_SYMBOLS for c in password):
        unmet.append("symbol")
    return unmet
