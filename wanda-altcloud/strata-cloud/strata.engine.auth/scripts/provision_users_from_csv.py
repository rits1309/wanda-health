"""Provision a CSV-supplied list of users, end to end — develop/uat only.

A sibling to ``provision_users.py``, not a replacement for it: this file
exists so a client-provided list of real user emails never has to be
hardcoded into a committed Python source list, and so a list too large to
create one-by-one via the CLI (a few users are fine by hand; a few dozen are
not) can be provisioned in one run. Unlike ``provision_users.py`` (which only
ever adopts a Cognito account Terraform already created), this script also
CREATES the Cognito account for any CSV email not already in the pool --
checked per email, so re-running the same CSV is safe: an existing account is
adopted as-is (its password is never touched), only a genuinely missing one
gets created.

A newly created account gets a random password set permanently (no email
invite, no first-login challenge) -- deliberately: Cognito's native
invite-email flow needs the login route to handle a NEW_PASSWORD_REQUIRED
challenge, which is a client-code change requiring the client's own
confirmation first. Until that lands, every generated password is printed
once at the end of the run for out-of-band handoff to each user -- there is
no other way to recover it afterwards, and no self-service change-password
screen in the app yet either, so treat that output as a credential list, not
a log line to scroll past.

No default CSV path is committed here -- real emails are PII, and a
committed file only grows in git history, it never shrinks, so even a
single-test-account placeholder was removed once it was no longer needed.
The list must always be supplied at run time, in priority order:

  1. ``USERS_CSV_CONTENT`` env var -- the raw CSV text (e.g. an ECS run-task
     container-override environment entry built from a workflow input).
  2. ``--csv PATH`` / ``USERS_CSV_PATH`` env var -- a local file path, for a
     one-off run via ECS Exec (write the CSV to a temp path in the session,
     then point this at it).

Expected columns (header row required, case-insensitive, any order):
``Email Address`` (required); ``Role`` (one of coach/member/admin, default
coach); ``First Name`` / ``Last Name`` (default derived from the email's
local part when absent); ``Password`` (optional -- see below).

Password handling for a NEW account (an already-existing account's password
is never touched, CSV column or not):

  - Column supplied and non-blank: that password is used as-is (after
    checking it against the pool's policy up front, so a typo'd column
    fails loudly before any Cognito call rather than a confusing
    InvalidPasswordException). Use this when you already know how the
    password will reach each user -- e.g. the client is handing you a
    completed sheet of email+password pairs for their own distribution,
    or you're assigning a shared/company-standard initial password.
  - Column omitted or blank: a random one is generated and printed ONCE at
    the end of the run (see below) -- the only place it's ever recoverable.

Refuses outright to run against prod -- same reasoning as
``provision_users.py``: provisioning real identities from an externally
supplied list is a materially different, higher-stakes decision than a
develop/uat test run, and deserves its own explicit design.

Run (from strata.engine.auth, inside the deployed container -- it needs the
real STRATA_DATABASE_URL/STRATA_COGNITO_USER_POOL_ID, not a local .env):

    python3 -m scripts.provision_users_from_csv --csv /tmp/users.csv
"""

import argparse
import asyncio
import csv
import io
import json
import os
import secrets
import string
import sys
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from strata_core.domains.kernel import COGNITO_SUB, UserProfile
from strata_identity.identifiers import adopt
from strata_identity.roles import GROUP_BY_ROLE, ROLE_ADMIN, ROLE_COACH, ROLE_MEMBER, assign_role

from strata_engine_auth.core.config import settings
from strata_engine_auth.core.passwords import COGNITO_SYMBOLS, validate_password_policy
from strata_engine_auth.services.cognito import admin_cognito

ROLE_BY_NAME = {"coach": ROLE_COACH, "member": ROLE_MEMBER, "admin": ROLE_ADMIN}


@dataclass(frozen=True)
class ProvisionedUser:
    email: str
    first_name: str
    last_name: str
    role: str
    # Only consulted for a NEW account; an existing one's password is never
    # touched. None means "generate one" (see module docstring).
    password: str | None = None
    timezone: str = "America/New_York"


def _guard() -> None:
    if settings.environment == "prod":
        raise SystemExit(
            "Refusing to run against prod. This script provisions real user "
            "identities from an externally supplied list -- fine for develop/uat "
            "test runs, a materially different decision for production. If prod "
            "support is genuinely wanted, it needs its own explicit design (e.g. "
            "an approval-gated workflow), not silent inheritance from this guard "
            "being loosened."
        )


def _row_value(row: dict[str, str], *names: str) -> str | None:
    lowered = {k.strip().lower(): v.strip() for k, v in row.items() if k}
    for name in names:
        value = lowered.get(name.lower())
        if value:
            return value
    return None


def _name_from_email(email: str) -> str:
    local_part = email.split("@", 1)[0]
    return local_part.replace(".", " ").replace("_", " ").title()


def _parse_users(csv_text: str) -> tuple[ProvisionedUser, ...]:
    reader = csv.DictReader(io.StringIO(csv_text))
    users: list[ProvisionedUser] = []
    for row in reader:
        email = _row_value(row, "Email Address", "Email")
        if not email:
            continue  # a blank row -- never a named failure worth stopping the run for
        role_name = (_row_value(row, "Role") or "coach").lower()
        if role_name not in ROLE_BY_NAME:
            raise ValueError(
                f"{email}: unknown role {role_name!r}; expected one of {sorted(ROLE_BY_NAME)}"
            )
        fallback_name = _name_from_email(email)
        first_name = _row_value(row, "First Name", "First") or fallback_name
        last_name = _row_value(row, "Last Name", "Last") or ""
        password = _row_value(row, "Password")
        if password is not None:
            # Checked against the CONFIGURED policy up front -- a typo'd
            # column fails loudly here, before any Cognito call, rather
            # than a confusing InvalidPasswordException mid-run (and
            # possibly after some earlier rows already succeeded).
            unmet = validate_password_policy(password, settings)
            if unmet:
                raise ValueError(f"{email}: supplied password fails policy: {', '.join(unmet)}")
        users.append(
            ProvisionedUser(email, first_name, last_name, ROLE_BY_NAME[role_name], password)
        )
    if not users:
        raise SystemExit(
            "No users found in the supplied CSV (checked for an 'Email Address' column)."
        )
    return tuple(users)


def _load_csv_text(csv_path: str | None) -> str:
    content = os.environ.get("USERS_CSV_CONTENT")
    if content:
        return content
    if not csv_path:
        raise SystemExit(
            "No CSV supplied -- set USERS_CSV_CONTENT, or pass --csv PATH / "
            "set USERS_CSV_PATH. There is no committed default file to fall "
            "back to (removed deliberately: real emails are PII)."
        )
    with open(csv_path, encoding="utf-8-sig") as handle:
        return handle.read()


def _generate_password() -> str:
    """A random password guaranteed to satisfy the pool's CONFIGURED policy
    (Settings) -- Cognito's own InvalidPasswordException is the drift
    backstop, same discipline as core/passwords.py's mirror. Space is
    deliberately excluded from the symbol pool: Cognito rejects a
    leading/trailing space outright, and there is no post-generation
    trim/re-check here, so a landable space is simply never offered."""
    length = max(settings.password_min_length, 16)
    required: list[str] = []
    pools: list[str] = []
    if settings.password_require_uppercase:
        required.append(secrets.choice(string.ascii_uppercase))
        pools.append(string.ascii_uppercase)
    if settings.password_require_lowercase:
        required.append(secrets.choice(string.ascii_lowercase))
        pools.append(string.ascii_lowercase)
    if settings.password_require_digit:
        required.append(secrets.choice(string.digits))
        pools.append(string.digits)
    if settings.password_require_symbol:
        symbol_pool = COGNITO_SYMBOLS.strip()
        required.append(secrets.choice(symbol_pool))
        pools.append(symbol_pool)
    if not pools:
        pools.append(string.ascii_letters + string.digits)
    all_chars = "".join(pools)
    chars = required + [secrets.choice(all_chars) for _ in range(length - len(required))]
    # Fisher-Yates using secrets.randbelow, not the random module -- keeps the
    # whole password CSPRNG-derived rather than only its individual characters.
    for i in range(len(chars) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    return "".join(chars)


def _create_user(idp: object, email: str, desired_password: str | None) -> tuple[str, str | None]:
    """AdminCreateUser + a permanent password, no email/challenge involved
    (see the module docstring for why). Username IS the email here --
    confirmed against the real develop pool (a live AdminCreateUser call
    with a UUID username failed: "Username should be an email"), because
    this pool is configured with `username_attributes = ["email"]`
    (modules/auth/cognito/main.tf), not `alias_attributes`. That's a
    different Cognito setting than the one services/cognito.py's own
    admin_create_user documents (its "never email-shaped" comment describes
    an alias pool, which is not what's actually deployed here).

    Returns (sub, generated_password) -- generated_password is None when
    `desired_password` was supplied (the caller already knows it, e.g. it
    came from the CSV's own Password column); set only when one had to be
    generated here, since that is the only case needing to be surfaced for
    out-of-band handoff.
    """
    response = idp.admin_create_user(
        UserPoolId=settings.cognito_user_pool_id,
        Username=email,
        MessageAction="SUPPRESS",
        UserAttributes=[
            {"Name": "email", "Value": email},
            {"Name": "email_verified", "Value": "true"},
        ],
    )
    found = {a["Name"]: a["Value"] for a in response["User"]["Attributes"]}
    generated_password = desired_password is None
    password = desired_password if desired_password is not None else _generate_password()
    idp.admin_set_user_password(
        UserPoolId=settings.cognito_user_pool_id, Username=email, Password=password, Permanent=True
    )
    return str(found["sub"]), (password if generated_password else None)


def _get_or_create_sub(
    idp: object, email: str, desired_password: str | None
) -> tuple[str, bool, str | None]:
    """The pool subject for `email`, creating the account if the CSV named
    someone Terraform/an earlier run hasn't. Returns (sub, created,
    generated_password): `created` distinguishes a brand-new account from
    an adopted existing one (whose password is never touched, CSV column or
    not); `generated_password` is set only when this call just created the
    account AND had to generate its password itself."""
    try:
        response = idp.admin_get_user(UserPoolId=settings.cognito_user_pool_id, Username=email)
    except idp.exceptions.UserNotFoundException:
        sub, generated_password = _create_user(idp, email, desired_password)
        return sub, True, generated_password
    attributes = {a["Name"]: a["Value"] for a in response["UserAttributes"]}
    return str(attributes["sub"]), False, None


async def _provision_one(
    session: AsyncSession, idp: object, user: ProvisionedUser
) -> tuple[str, str | None]:
    sub, created, generated_password = _get_or_create_sub(idp, user.email, user.password)

    profile = (
        (
            await session.execute(
                select(UserProfile)
                .where(UserProfile.email == user.email)
                .order_by(UserProfile.created_at, UserProfile.id)
            )
        )
        .scalars()
        .first()
    )
    if profile is None:
        profile = UserProfile(
            email=user.email,
            first_name=user.first_name,
            last_name=user.last_name,
            timezone=user.timezone,
        )
        session.add(profile)
        await session.flush()

    await adopt(session, user_id=profile.id, type_name=COGNITO_SUB, external_id=sub, exclusive=True)
    granted = await assign_role(session, profile.id, user.role)

    # user_roles is authoritative; Cognito group membership must mirror it --
    # /v1/auth/me resolves the caller's roles from Cognito group membership,
    # NOT user_roles directly (the same gap provision_users.py hit in UAT).
    idp.admin_add_user_to_group(
        UserPoolId=settings.cognito_user_pool_id,
        Username=user.email,
        GroupName=GROUP_BY_ROLE[user.role],
    )

    if created:
        status = (
            "newly created (generated password)"
            if generated_password
            else "newly created (supplied password)"
        )
    elif granted:
        status = "granted"
    else:
        status = "already held"
    line = f"{user.email} -> profile {profile.id}, role={user.role} ({status})"
    return line, generated_password


def _new_accounts_json_line(new_accounts: list[tuple[str, str]]) -> str:
    """A single, greppable machine-readable line for automation (e.g. a
    health-check step that logs into each brand-new account to confirm it
    actually works) -- deliberately separate from the human-readable block
    above it so a wrapper never has to regex-parse indented prose to get at
    the password. Emitted even when empty ("[]"), so a caller doesn't have
    to guess whether this line exists."""
    return "NEW_ACCOUNTS_JSON:" + json.dumps([{"email": e, "password": p} for e, p in new_accounts])


async def main(csv_path: str | None) -> None:
    _guard()
    users = _parse_users(_load_csv_text(csv_path))
    idp = admin_cognito()
    engine = create_async_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    new_accounts: list[tuple[str, str]] = []
    try:
        async with sessionmaker() as session:
            for user in users:
                line, new_password = await _provision_one(session, idp, user)
                print(line)
                if new_password:
                    new_accounts.append((user.email, new_password))
            await session.commit()
    finally:
        await engine.dispose()
    print(f"\nProvisioned {len(users)} user(s) against {settings.environment}.")

    if new_accounts:
        print(
            f"\n{len(new_accounts)} brand-new account(s) were created with a generated "
            "password -- there is no email invite and no self-service change-password "
            "screen yet, so this is the ONLY place this run exposes them. Hand each one "
            "to its user out-of-band and treat this block as a credential list, not a "
            "log line to scroll past:"
        )
        for email, password in new_accounts:
            print(f"  {email}: {password}")

    print(_new_accounts_json_line(new_accounts))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        default=os.environ.get("USERS_CSV_PATH"),
        help="Path to the users CSV (ignored if USERS_CSV_CONTENT is set; "
        "required otherwise, no committed default file)",
    )
    args = parser.parse_args()
    try:
        asyncio.run(main(args.csv))
    except Exception as exc:  # noqa: BLE001 -- a one-off ops script: surface any failure plainly and fail the pipeline
        print(f"::error::{exc}", file=sys.stderr)
        sys.exit(1)
