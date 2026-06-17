"""Authentication boundaries for page sessions, Runner, and Sonic callbacks.

All auth logic migrated from the monolithic midscene-upload.py handler so that
the new task_server package can validate requests without depending on the
single-file server.
"""

import base64
import hashlib
import json
import secrets
import time

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

# ---------------------------------------------------------------------------
# Revoked session token store (in-process, lost on restart – acceptable for now)
# ---------------------------------------------------------------------------
REVOKED_SESSION_TOKENS: set = set()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sign_session_payload(payload):
    """Sign a JSON payload with the session secret, returning ``body.signature``."""
    body = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    sig = hashlib.sha256((body + TASK_SESSION_SECRET).encode("utf-8")).hexdigest()
    return f"{body}.{sig}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def bearer_token(headers):
    """Extract Bearer token from *Authorization* header."""
    value = (headers or {}).get("Authorization", "")
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return ""


def verify_password(username, password):
    """Verify *username* / *password* credentials.

    Supports two modes (matching the original ``task_password_valid``):
    * ``TASK_ADMIN_PASSWORD_HASH`` – sha256 hex digest of the password.
    * ``TASK_ADMIN_PASSWORD`` – plaintext comparison (fallback).

    Returns ``True`` only when *username* matches ``TASK_ADMIN_USER`` **and**
    the password is valid.
    """
    if username != TASK_ADMIN_USER:
        return False
    raw = password or ""
    if TASK_ADMIN_PASSWORD_HASH:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return secrets.compare_digest(digest, TASK_ADMIN_PASSWORD_HASH)
    if TASK_ADMIN_PASSWORD:
        return secrets.compare_digest(raw, TASK_ADMIN_PASSWORD)
    return False


def create_session_token():
    """Create a new session token for ``TASK_ADMIN_USER``.

    Returns the signed token string (``base64_payload.signature``).
    """
    now = int(time.time())
    return _sign_session_payload({
        "user": TASK_ADMIN_USER,
        "iat": now,
        "exp": now + max(300, TASK_SESSION_TTL_SECONDS),
        "nonce": secrets.token_hex(12),
    })


def verify_session_token(token):
    """Verify a session token.

    Checks signature integrity, expiry (TTL), and that the embedded user
    matches ``TASK_ADMIN_USER``.  Also rejects tokens in the revoke set.

    Returns the decoded payload dict on success, or ``None`` on failure.
    """
    token = (token or "").strip()
    if not token or token in REVOKED_SESSION_TOKENS or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    expected = hashlib.sha256((body + TASK_SESSION_SECRET).encode("utf-8")).hexdigest()
    if not secrets.compare_digest(sig, expected):
        return None
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(
            base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        )
    except Exception:
        return None
    if safe_int(payload.get("exp"), 0) < int(time.time()):
        return None
    if payload.get("user") != TASK_ADMIN_USER:
        return None
    return payload


def login(username, password):
    """Authenticate and return ``(success, token_or_error)``.

    On success returns ``(True, token_string)``; on failure returns
    ``(False, error_message)``.
    """
    if not verify_password(username, password):
        return False, "账号或密码错误"
    token = create_session_token()
    return True, token


def logout(token):
    """Revoke *token* so it can no longer be used.

    This is a no-op when *token* is empty/falsy.
    """
    if token:
        REVOKED_SESSION_TOKENS.add(token)


def is_user_authorized(headers):
    """Check whether the request is from an authenticated user.

    Accepts either an ``x-token`` matching the Runner token **or** a valid
    Bearer session token.
    """
    if (headers or {}).get("x-token", "") == TOKEN:
        return True
    return bool(verify_session_token(bearer_token(headers)))


def is_runner_authorized(headers):
    """Check whether the request carries the Runner token via ``x-token``."""
    return (headers or {}).get("x-token", "") == TOKEN


def is_sonic_callback_authorized(headers):
    """Check whether the request carries the Sonic callback token via ``x-token``."""
    return (headers or {}).get("x-token", "") == SONIC_CALLBACK_TOKEN


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
    if qtoken in (TOKEN, SONIC_CALLBACK_TOKEN):
        print(
            "WARNING: query token auth is deprecated; use x-token or Authorization header",
            flush=True,
        )
        return True
    return False
