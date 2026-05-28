"""JWT verification interface for the cloud deployment mode.

This module is *only* used when ``KNOWRA_DEPLOY_MODE == "cloud"``. In local
desktop mode the backend has no concept of authenticated users — calls to
``current_user()`` return the implicit single-user identity.

Design intent:
  - Keep the verification surface thin and synchronous so it can be plugged
    into FastAPI ``Depends(...)`` without async fan-out.
  - The cloud verification logic delegates to Supabase Auth's JWKS endpoint
    (resolved once at startup, then cached), so we don't depend on the
    Supabase Python SDK for the hot path.
  - On token failures we raise a typed exception that the router layer
    translates to a 401/403 with the unified error envelope described in
    docs/SYNC-PROTOCOL.md.

⚠️ Phase 0 deliverable: this file ships the *interface* and a minimal mock
implementation. The real verification (JWKS resolution, signature check,
expiry, audience claim, etc.) lands in Phase 1 W2. Anything calling
``verify_jwt`` after Phase 0 must check the docstring on each helper for
"NOT IMPLEMENTED" markers.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Protocol


# ── public exceptions ───────────────────────────────────────────────────


class AuthError(Exception):
    """Base class for all auth failures."""

    error_code: str = "unauthorized"
    http_status: int = 401


class TokenExpired(AuthError):
    error_code = "token_expired"


class TokenInvalid(AuthError):
    error_code = "token_invalid"


class TokenMissing(AuthError):
    error_code = "token_missing"


class UserDisabled(AuthError):
    error_code = "forbidden"
    http_status = 403


# ── data shapes ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AuthenticatedUser:
    """The contract that downstream routers see.

    Carries the minimum identity needed for tenant isolation. Anything
    richer (display_name, settings) goes through the ``user_profiles``
    table to keep this object tiny and the verification fast."""

    user_id: str  # UUID string (matches Supabase auth.users.id)
    email: Optional[str]
    role: str = "authenticated"  # Supabase JWT 'role' claim


# ── interface (the only thing routers should depend on) ────────────────


class JwtVerifier(Protocol):
    """Anyone wanting to plug in an alternative auth backend implements
    this protocol. Routers should depend on the interface, not on the
    concrete Supabase implementation."""

    def verify(self, token: str) -> AuthenticatedUser:
        """Verify ``token`` and return the user, or raise AuthError.

        Implementation MUST:
          - reject tokens with no/invalid signature → TokenInvalid
          - reject tokens past ``exp`` → TokenExpired
          - reject tokens with missing/wrong ``aud`` → TokenInvalid
          - reject tokens whose ``role`` claim is ``anon`` → TokenInvalid
          - never silently widen the user identity (e.g. by trusting
            ``user_id`` claims when ``sub`` is empty)
        """
        ...


# ── concrete implementations ───────────────────────────────────────────


class SupabaseJwtVerifier:
    """Real verifier backed by the Supabase project's JWKS.

    ⚠️ NOT IMPLEMENTED in Phase 0. The body below documents the intended
    construction and request shape so the rest of the codebase can wire
    against it. Phase 1 W2 finishes the implementation.

    Construction:
        verifier = SupabaseJwtVerifier(
            project_url="https://xxxxx.supabase.co",
            jwt_secret=os.environ["SUPABASE_JWT_SECRET"],
        )

    Verification (intended):
        1. Strip the leading ``Bearer `` prefix.
        2. Decode the JWT header to read ``kid``.
        3. Pull the JWKS from ``{project_url}/auth/v1/jwks`` (cached for
           ``JWKS_CACHE_TTL`` seconds).
        4. Verify signature with the matching key.
        5. Check ``exp``, ``aud == "authenticated"``, ``role != "anon"``.
        6. Build ``AuthenticatedUser(user_id=claims["sub"], email=...)``.
    """

    JWKS_CACHE_TTL = 600

    def __init__(self, *, project_url: str, jwt_secret: Optional[str] = None) -> None:
        self._project_url = project_url
        self._jwt_secret = jwt_secret

    def verify(self, token: str) -> AuthenticatedUser:  # noqa: D401
        raise NotImplementedError(
            "SupabaseJwtVerifier.verify is Phase 1 work — see model_gateway/auth.py"
        )


class LocalSingleUserVerifier:
    """Identity used in local desktop mode.

    Returns a fixed ``AuthenticatedUser`` so router code that depends on
    ``current_user`` can be shared between local and cloud deployments
    without branching. The ``user_id`` is read from the SQLite
    ``user_profiles`` table (or auto-generated on first run).
    """

    def __init__(self, *, user_id: str, email: Optional[str] = None) -> None:
        self._user = AuthenticatedUser(user_id=user_id, email=email)

    def verify(self, token: str) -> AuthenticatedUser:
        # Local mode ignores the token entirely; the deploy mode guard in
        # the router decides whether to even call verify().
        return self._user


# ── module-level lookup ────────────────────────────────────────────────


def get_verifier() -> JwtVerifier:
    """Return the verifier appropriate for the current deploy mode.

    Reads ``KNOWRA_DEPLOY_MODE`` (default ``local``) plus the matching
    config fields. Cached at first call.
    """
    mode = os.environ.get("KNOWRA_DEPLOY_MODE", "local").lower()
    if mode == "cloud":
        project_url = os.environ["SUPABASE_PROJECT_URL"]
        jwt_secret = os.environ.get("SUPABASE_JWT_SECRET")
        return SupabaseJwtVerifier(project_url=project_url, jwt_secret=jwt_secret)
    # local fallback
    local_user_id = os.environ.get("KNOWRA_LOCAL_USER_ID", "00000000-0000-0000-0000-000000000000")
    return LocalSingleUserVerifier(user_id=local_user_id)


# ── convenience helper used by FastAPI dependencies ────────────────────


def verify_jwt(authorization_header: Optional[str]) -> AuthenticatedUser:
    """Extract ``Authorization: Bearer <token>`` and verify it.

    Routers should wrap this in a ``Depends(...)`` so they can declare a
    type-checked ``AuthenticatedUser`` parameter rather than poking at
    headers themselves.
    """
    if not authorization_header:
        raise TokenMissing("Authorization header is required")
    parts = authorization_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise TokenInvalid("Authorization header must be 'Bearer <token>'")
    return get_verifier().verify(parts[1])
