"""Authentication boundaries for page sessions, Runner, and Sonic callbacks.

All auth logic migrated from the monolithic midscene-upload.py handler so that
the new task_server package can validate requests without depending on the
single-file server.
"""

import secrets

from .config import (
    ALLOW_QUERY_TOKEN,
    SONIC_CALLBACK_TOKEN,
    TASK_ADMIN_PASSWORD,
    TASK_ADMIN_PASSWORD_HASH,
    TASK_ADMIN_USER,
    TASK_SESSION_SECRET,
    TASK_SESSION_TTL_SECONDS,
    TOKEN,
    safe_int,
)


class _PersistentRevocations:
    """Compatibility for the old router's ``REVOKED_SESSION_TOKENS.add`` call."""

    def add(self, token):
        logout(token)


REVOKED_SESSION_TOKENS = _PersistentRevocations()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def bearer_token(headers):
    """Extract Bearer token from *Authorization* header."""
    value = (headers or {}).get("Authorization", "")
    if isinstance(value, str) and value.lower().startswith("bearer "):
        return value[7:].strip()
    return ""


def verify_password(username, password):
    """Verify a local account, including persistent login throttling."""
    from . import identity

    try:
        return bool(identity.get_identity_store().authenticate(username, password))
    except identity.IdentityError:
        return False


def create_session_token(user=None):
    """Issue an opaque token for an existing user (trusted internal callers only)."""
    from . import identity

    return identity.get_identity_store().create_session(TASK_ADMIN_USER if user is None else user)


def verify_session_token(token):
    """Resolve an unexpired, active session against current persistent identity."""
    from . import identity

    if not isinstance(token, str) or not token.strip():
        return None
    return identity.get_identity_store().verify_session(token.strip())


def login(username, password):
    """Authenticate and return ``(success, token_or_error)``.

    On success returns ``(True, token_string)``; on failure returns
    ``(False, error_message)``.
    """
    from . import identity

    try:
        result = identity.get_identity_store().login(username, password)
        return (True, result["token"]) if result else (False, "账号或密码错误")
    except identity.IdentityError as exc:
        return False, str(exc)


def logout(token):
    """Revoke *token* so it can no longer be used.

    This is a no-op when *token* is empty/falsy.
    """
    from . import identity

    if token:
        identity.get_identity_store().logout(token)


def is_user_authorized(headers):
    """Check whether the request is from an authenticated user.

    Accepts either an ``x-token`` matching the Runner token **or** a valid
    Bearer session token.
    """
    if is_runner_authorized(headers):
        return True
    return bool(verify_session_token(bearer_token(headers)))


def is_runner_authorized(headers):
    """Check whether the request carries the Runner token via ``x-token``."""
    value = (headers or {}).get("x-token", "")
    return bool(TOKEN and isinstance(value, str) and secrets.compare_digest(value.encode(), TOKEN.encode()))


def is_sonic_callback_authorized(headers):
    """Check whether the request carries the Sonic callback token via ``x-token``."""
    value = (headers or {}).get("x-token", "")
    return bool(SONIC_CALLBACK_TOKEN and isinstance(value, str) and secrets.compare_digest(value.encode(), SONIC_CALLBACK_TOKEN.encode()))


def is_authorized_with_query(headers, qs):
    """Authorise a request, with fallback to a query-string *token* parameter.

    This mirrors the original ``_authorized_with_qs`` method:

    1. First tries ``is_sonic_callback_authorized`` or ``is_user_authorized``.
    2. If those fail **and** ``ALLOW_QUERY_TOKEN`` is enabled, accepts a
       ``token`` query parameter equal to either ``TOKEN`` or
       ``SONIC_CALLBACK_TOKEN`` (and prints a deprecation warning).
    3. Otherwise returns ``False``.
    """
    if is_sonic_callback_authorized(headers) or is_user_authorized(headers):
        return True
    if not ALLOW_QUERY_TOKEN:
        return False
    qtoken = (qs or {}).get("token", "")
    if qtoken and qtoken in (TOKEN, SONIC_CALLBACK_TOKEN):
        print(
            "WARNING: query token auth is deprecated; use x-token or Authorization header",
            flush=True,
        )
        return True
    return False
