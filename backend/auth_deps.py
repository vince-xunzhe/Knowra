"""FastAPI dependencies for authentication.

Routers should depend on :func:`current_user` rather than reaching into
``model_gateway.auth`` directly so the deploy-mode branching stays
centralized.

Two flavors:

- :func:`current_user` — required: raises 401 if no/invalid token.
- :func:`current_user_optional` — soft: returns ``None`` rather than raising,
  used by endpoints that have different behavior for guest vs. logged-in
  users (none today; reserved).

In local desktop mode the verifier always returns the implicit local
user, so ``current_user`` succeeds even without an Authorization header.
This keeps router code identical between deploy modes.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, status

from config import is_cloud_mode
from model_gateway.auth import (
    AuthenticatedUser,
    AuthError,
    TokenMissing,
    verify_jwt,
)


def current_user(
    authorization: Optional[str] = Header(default=None),
) -> AuthenticatedUser:
    """Resolve the authenticated user for this request.

    In cloud mode the Authorization header is required; missing or
    invalid → HTTP 401 with the unified error envelope.

    In local mode the verifier ignores the header and returns the
    implicit single-user identity, so routers can declare the dependency
    unconditionally without changing behavior on the desktop.
    """
    if not is_cloud_mode():
        # Local: the verifier returns a static identity. We still call
        # verify_jwt with a dummy header so the code path is uniform.
        try:
            return verify_jwt(authorization or "Bearer local")
        except AuthError:  # pragma: no cover - local mode never raises
            # Falling through here would mean a misconfiguration of the
            # local verifier; surface it loudly.
            raise
    try:
        return verify_jwt(authorization)
    except TokenMissing as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": exc.error_code,
                "message": str(exc),
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    except AuthError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={
                "error": exc.error_code,
                "message": str(exc),
            },
            headers=(
                {"WWW-Authenticate": "Bearer"}
                if exc.http_status == status.HTTP_401_UNAUTHORIZED
                else {}
            ),
        )


def current_user_optional(
    authorization: Optional[str] = Header(default=None),
) -> Optional[AuthenticatedUser]:
    """Same as :func:`current_user` but returns ``None`` instead of
    raising when there's no valid token. Useful for endpoints that want
    to behave differently for anonymous callers (none today)."""
    if not authorization:
        return None
    try:
        return verify_jwt(authorization)
    except AuthError:
        return None


__all__ = [
    "current_user",
    "current_user_optional",
    "AuthenticatedUser",
]
