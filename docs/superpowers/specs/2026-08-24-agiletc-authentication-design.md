# AgileTC Read-Only Authentication Design

## Goal

Keep the existing AgileTC case-set metadata integration working after the case
platform enables dynamic verification. The Midscene task platform must obtain
read-only access without copying AgileTC `caseContent` into generated reports.

## Scope

The integration continues to read only the metadata already consumed by test
reports:

- case-set id and title;
- description and inferred version;
- requirement link and AgileTC link;
- creator, modifier, created time, and updated time;
- execution-record count.

The change does not read, persist, or render the full mind-map content,
preconditions, steps, expected results, or test data.

## Authentication Modes

The HTTP client supports three modes, in this order:

1. A configured read-only access token is attached to every request.
2. A service account logs in with username, password, and a current TOTP code,
   then reuses the returned cookies.
3. Anonymous access remains available only when authentication is not required.

Production can set `CASE_PLATFORM_AUTH_REQUIRED=true` to reject missing or
invalid authentication instead of silently falling back to anonymous access.

## Configuration

The client reads credentials only from process environment variables:

- `CASE_PLATFORM_ACCESS_TOKEN`
- `CASE_PLATFORM_TOKEN_HEADER` (default `Authorization`)
- `CASE_PLATFORM_TOKEN_PREFIX` (default `Bearer`)
- `CASE_PLATFORM_USERNAME`
- `CASE_PLATFORM_PASSWORD`
- `CASE_PLATFORM_TOTP_SECRET`
- `CASE_PLATFORM_LOGIN_PATH` (default `/api/user/login`)
- `CASE_PLATFORM_TOTP_FIELD` (default `totpCode`)
- `CASE_PLATFORM_AUTH_REQUIRED`

Secrets must not be committed, returned from APIs, interpolated into exception
messages, or written to logs. The exposed TOTP seed must be rotated before the
production environment is configured.

## Client Behavior

`CasePlatformClient` owns request authentication and JSON transport:

- Token mode adds the configured header without logging its value.
- TOTP mode generates the current RFC 6238 SHA-1 code and posts the login JSON.
- Authenticated modes always require HTTPS.
- Redirects are allowed only within the original scheme, host, and port.
- A cookie-aware opener is reused across metadata list/detail requests.
- HTTP 401/403 or AgileTC code `100011` invalidates the cookie session.
- Credential mode re-authenticates and retries the failed request once.
- Token mode reports an explicit authentication failure because a static token
  cannot be refreshed by this process.
- Login failures expose only a sanitized platform message.

The existing search service delegates transport to this client and retains its
normalization, detail fallback, and report metadata behavior.

## Permission Boundary

The AgileTC service account should be granted only:

- `POST /api/user/login`
- `GET /api/case/list*`
- `GET /api/case/detail*`

It must not receive case creation, update, deletion, execution-record, user, or
administration permissions.

## Verification

Automated tests cover:

- deterministic RFC 6238 TOTP generation;
- configured token header attachment;
- TOTP login payload and cookie reuse;
- one re-login after an expired session;
- one coordinated re-login when concurrent requests observe the same expired session;
- rejection of plaintext authenticated transport and cross-origin redirects;
- required-auth configuration failures;
- sanitization of login errors;
- unchanged AgileTC metadata normalization.

The production deployment must additionally verify a real read-only search with
the rotated credential stored in `/opt/midscene.env`.
