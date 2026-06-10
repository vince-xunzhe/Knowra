"""JWT verification for the cloud deployment mode.

This module is used by routers (via ``current_user`` FastAPI dependency)
to authenticate incoming requests against Supabase Auth's JWTs.

Design intent:
  - Keep the verification surface thin and synchronous so it can be
    plugged into FastAPI ``Depends(...)`` without async fan-out.
  - Support BOTH JWT signing schemes Supabase has shipped:
      * HS256 — legacy "JWT Secret" shared secret. Pre-2025 default.
      * ES256 (and RS256) — asymmetric, served via the project's JWKS
        endpoint. The default for projects created after Supabase's
        2025 key rotation. Verification fetches the public key from
        ``{project_url}/auth/v1/.well-known/jwks.json``, with PyJWT's
        built-in PyJWKClient handling the kid → key lookup + cache.
    The algorithm is picked off the token's own ``alg`` header so a
    single deploy handles both old and new projects without config
    flags.
  - On token failures we raise a typed exception that the router layer
    translates to a 401/403 with the unified error envelope described
    in docs/SYNC-PROTOCOL.md.
  - In local desktop mode the backend has no concept of authenticated
    users — ``current_user`` returns the implicit single-user identity
    bound at startup via the env var ``KNOWRA_LOCAL_USER_ID``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Protocol

import jwt as pyjwt
from jwt import PyJWKClient
# PyJWT already classifies its algorithms — reuse rather than re-listing
# the asymmetric ones here. Adding EdDSA / PS256 etc. to our allow-list
# in the future then "just works" without touching membership checks.
from jwt.algorithms import requires_cryptography as _ASYMMETRIC_ALGS


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
    """Verify Supabase Auth JWTs across both signing schemes.

    Pre-2025 Supabase signed every session JWT with HS256 + a shared
    project secret (Project Settings → API → "Legacy JWT Secret").
    Post-2025 the default is ES256 (asymmetric, ECC P-256), with the
    public key served from JWKS:
        ``{project_url}/auth/v1/.well-known/jwks.json``

    A single deploy supports both — we pick the algorithm off the
    token's own ``alg`` header and dispatch:
      - HS256 → verify with ``jwt_secret`` (if supplied)
      - ES256 / RS256 → verify with the JWKS-fetched public key for
        the token's ``kid``

    Construction:
        # Brand-new (ES256-only) Supabase project — JWKS is enough:
        verifier = SupabaseJwtVerifier(project_url="https://xxx.supabase.co")

        # Legacy or hybrid project — also supply HS256 secret:
        verifier = SupabaseJwtVerifier(
            project_url="https://xxx.supabase.co",
            jwt_secret=os.environ["SUPABASE_JWT_SECRET"],
        )

    Verification chain (in order; first failure short-circuits):
        1. Pick alg from token header → validate against allow-list
        2. PyJWT signature + ``exp`` validation with the matching key
        3. Audience must equal ``audience`` (default "authenticated")
        4. Role must not be ``anon`` (the public/no-login role)
        5. ``sub`` claim must be a non-empty UUID-shaped string
    """

    DEFAULT_AUDIENCE = "authenticated"
    # Allow-list. Anything else (e.g. ``none``) is rejected before we
    # even look at the signature — defends against alg-confusion attacks.
    DEFAULT_ALGORITHMS = ("HS256", "ES256", "RS256")

    def __init__(
        self,
        *,
        project_url: str,
        jwt_secret: str = "",
        audience: str = DEFAULT_AUDIENCE,
        algorithms: tuple[str, ...] = DEFAULT_ALGORITHMS,
        leeway_seconds: int = 30,
        jwks_lifespan_seconds: int = 3600,
        jwks_client: Optional[PyJWKClient] = None,
    ) -> None:
        if not project_url and not jwt_secret:
            # Both empty → we have no way to verify anything. Keep this
            # explicit so misconfigurations surface at boot, not on the
            # first authenticated request.
            raise ValueError(
                "SupabaseJwtVerifier needs at least one of project_url "
                "(for JWKS / ES256) or jwt_secret (for legacy HS256). "
                "Set SUPABASE_PROJECT_URL and/or SUPABASE_JWT_SECRET."
            )
        self._project_url = (project_url or "").rstrip("/")
        self._jwt_secret = jwt_secret
        self._audience = audience
        self._algorithms = list(algorithms)
        self._leeway = leeway_seconds
        # PyJWKClient caches keys in-process; cache-control headers from
        # Supabase keep it warm. Lazily instantiated so HS256-only test
        # setups don't require a reachable network. Tests can pass in
        # their own stub via ``jwks_client=`` to avoid the HTTP fetch
        # without poking private state.
        self._jwks_client: Optional[PyJWKClient] = jwks_client
        self._jwks_lifespan = jwks_lifespan_seconds

    def _get_jwks_client(self) -> PyJWKClient:
        if self._jwks_client is None:
            if not self._project_url:
                # Reached when a token's alg is asymmetric but the
                # operator only configured HS256. We surface as
                # TokenInvalid (not a config error) because the cause
                # is the incoming request, not the deployment state.
                raise TokenInvalid(
                    "received asymmetric token but JWKS endpoint not "
                    "configured (SUPABASE_PROJECT_URL is empty)"
                )
            jwks_url = f"{self._project_url}/auth/v1/.well-known/jwks.json"
            self._jwks_client = PyJWKClient(
                jwks_url,
                cache_keys=True,
                max_cached_keys=16,
                lifespan=self._jwks_lifespan,
            )
        return self._jwks_client

    def _resolve_key_and_alg(self, token: str) -> tuple[object, str]:
        """Pick the verification key based on the token's header.

        Returns ``(key, alg)`` where ``key`` is either the HS256 shared
        secret (str) or a PyJWK-fetched public key. Raises TokenInvalid
        if the algorithm isn't on our allow-list or we have no key for
        it.
        """
        try:
            unverified_header = pyjwt.get_unverified_header(token)
        except pyjwt.DecodeError as exc:
            raise TokenInvalid(f"malformed token header: {exc}") from exc

        alg = str(unverified_header.get("alg") or "").upper()
        if alg not in self._algorithms:
            raise TokenInvalid(
                f"unsupported alg {alg!r}; allowed: {self._algorithms}"
            )

        if alg == "HS256":
            if not self._jwt_secret:
                raise TokenInvalid(
                    "received HS256 token but no jwt_secret configured "
                    "(set SUPABASE_JWT_SECRET to the Legacy JWT Secret)"
                )
            return self._jwt_secret, alg

        # Anything else on our allow-list is asymmetric (verified by
        # PyJWT's own ``requires_cryptography`` set). If the operator
        # ever adds a symmetric non-HS256 alg to ``algorithms``, this
        # assert fires loudly rather than silently routing it through
        # JWKS where it doesn't belong.
        assert alg in _ASYMMETRIC_ALGS, f"alg {alg!r} on allow-list but not asymmetric"
        try:
            signing_key = self._get_jwks_client().get_signing_key_from_jwt(token)
        except pyjwt.PyJWKClientError as exc:
            raise TokenInvalid(f"JWKS lookup failed: {exc}") from exc
        return signing_key.key, alg

    def verify(self, token: str) -> AuthenticatedUser:
        if not token:
            raise TokenInvalid("empty token")

        key, alg = self._resolve_key_and_alg(token)
        try:
            claims = pyjwt.decode(
                token,
                key,
                algorithms=[alg],
                audience=self._audience,
                leeway=self._leeway,
            )
        except pyjwt.ExpiredSignatureError as exc:
            raise TokenExpired(str(exc) or "token expired") from exc
        except pyjwt.InvalidAudienceError as exc:
            raise TokenInvalid(f"audience mismatch: {exc}") from exc
        except pyjwt.InvalidSignatureError as exc:
            raise TokenInvalid("signature verification failed") from exc
        except pyjwt.DecodeError as exc:
            raise TokenInvalid(f"malformed token: {exc}") from exc
        except pyjwt.InvalidTokenError as exc:
            # Catch-all for any other PyJWT failure mode (immature exp,
            # missing required claim, etc.).
            raise TokenInvalid(str(exc) or "invalid token") from exc

        # Additional invariants enforced after signature is trusted.
        role = (claims.get("role") or "").strip()
        if role == "anon" or not role:
            raise TokenInvalid("anonymous role not permitted")

        sub = (claims.get("sub") or "").strip()
        if not sub:
            raise TokenInvalid("missing sub claim")

        return AuthenticatedUser(
            user_id=sub,
            email=claims.get("email"),
            role=role,
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


_VERIFIER_CACHE: Optional[JwtVerifier] = None


def get_verifier() -> JwtVerifier:
    """Return the verifier appropriate for the current deploy mode.

    Reads ``KNOWRA_DEPLOY_MODE`` (default ``local``) plus the matching
    config fields. The first call instantiates the verifier; subsequent
    calls return the cached instance.

    Tests can reset the cache via ``reset_verifier_cache()``.
    """
    global _VERIFIER_CACHE
    if _VERIFIER_CACHE is not None:
        return _VERIFIER_CACHE

    mode = os.environ.get("KNOWRA_DEPLOY_MODE", "local").lower()
    if mode == "cloud":
        project_url = os.environ.get("SUPABASE_PROJECT_URL", "")
        # JWT_SECRET is OPTIONAL now — ES256 projects authenticate via
        # JWKS, no shared secret needed. We keep accepting it for
        # legacy HS256 projects + hybrid setups (the verifier
        # dispatches per token).
        jwt_secret = os.environ.get("SUPABASE_JWT_SECRET", "")
        if not project_url:
            raise RuntimeError(
                "cloud mode requires SUPABASE_PROJECT_URL (used both "
                "to call the auth API and to fetch JWKS for ES256 "
                "tokens). SUPABASE_JWT_SECRET is optional; supply it "
                "if your project still issues HS256 tokens."
            )
        _VERIFIER_CACHE = SupabaseJwtVerifier(
            project_url=project_url,
            jwt_secret=jwt_secret,
        )
    else:
        # local fallback: implicit single-user identity, no token check.
        local_user_id = os.environ.get(
            "KNOWRA_LOCAL_USER_ID",
            "00000000-0000-0000-0000-000000000000",
        )
        _VERIFIER_CACHE = LocalSingleUserVerifier(user_id=local_user_id)
    return _VERIFIER_CACHE


def reset_verifier_cache() -> None:
    """Drop the cached verifier instance. Used by tests; not part of the
    runtime API."""
    global _VERIFIER_CACHE
    _VERIFIER_CACHE = None


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
