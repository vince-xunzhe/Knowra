"""Phase 1 W2: JWT verification + FastAPI current_user dependency tests.

Covers the three categories required by docs/PHASE-PLAN.md W2:
  1. Valid token → returns the right AuthenticatedUser
  2. Expired token → TokenExpired
  3. Tampered token → TokenInvalid

Plus deploy-mode behavior (local vs cloud) and the FastAPI dependency
shim around the verifier.
"""
import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import patch

import jwt as pyjwt

ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

from model_gateway import auth as auth_mod
from model_gateway.auth import (
    AuthenticatedUser,
    LocalSingleUserVerifier,
    SupabaseJwtVerifier,
    TokenExpired,
    TokenInvalid,
    TokenMissing,
    reset_verifier_cache,
    verify_jwt,
)


JWT_SECRET = "test-secret-do-not-use-in-prod"
PROJECT_URL = "https://test.supabase.co"


def _make_token(
    *,
    sub: str = "00000000-0000-0000-0000-deadbeef1234",
    email: str = "user@example.com",
    role: str = "authenticated",
    audience: str = "authenticated",
    exp_offset: int = 3600,
    key=JWT_SECRET,
    algorithm: str = "HS256",
    headers: Optional[dict] = None,
    extra_claims: Optional[dict] = None,
) -> str:
    """Build a JWT for tests. Defaults to HS256 + shared secret; pass
    ``key=<private_key>`` + ``algorithm="ES256"`` for asymmetric tokens.
    ``headers`` is forwarded to ``pyjwt.encode`` for things like
    ``{"kid": ...}``."""
    now = int(time.time())
    payload = {
        "sub": sub,
        "email": email,
        "role": role,
        "aud": audience,
        "iat": now,
        "exp": now + exp_offset,
    }
    if extra_claims:
        payload.update(extra_claims)
    return pyjwt.encode(payload, key, algorithm=algorithm, headers=headers)


# ── Token classification ──────────────────────────────────────────────


class TestSupabaseJwtVerifierAccepts(unittest.TestCase):
    def setUp(self) -> None:
        self.v = SupabaseJwtVerifier(
            project_url=PROJECT_URL,
            jwt_secret=JWT_SECRET,
        )

    def test_valid_token_returns_user(self):
        token = _make_token()
        user = self.v.verify(token)
        self.assertIsInstance(user, AuthenticatedUser)
        self.assertEqual(user.user_id, "00000000-0000-0000-0000-deadbeef1234")
        self.assertEqual(user.email, "user@example.com")
        self.assertEqual(user.role, "authenticated")

    def test_valid_token_within_leeway_still_accepted(self):
        # Token expired 10s ago is still inside the default 30s leeway.
        token = _make_token(exp_offset=-10)
        user = self.v.verify(token)
        self.assertEqual(user.user_id, "00000000-0000-0000-0000-deadbeef1234")


class TestSupabaseJwtVerifierRejects(unittest.TestCase):
    def setUp(self) -> None:
        self.v = SupabaseJwtVerifier(
            project_url=PROJECT_URL,
            jwt_secret=JWT_SECRET,
        )

    def test_expired_token_raises_token_expired(self):
        # Past the 30s leeway → real expiry.
        token = _make_token(exp_offset=-3600)
        with self.assertRaises(TokenExpired):
            self.v.verify(token)

    def test_tampered_signature_rejected(self):
        token = _make_token()
        # Flip a byte in the signature segment.
        head, payload, sig = token.split(".")
        tampered = f"{head}.{payload}.{sig[:-2]}AA"
        with self.assertRaises(TokenInvalid):
            self.v.verify(tampered)

    def test_wrong_secret_rejected(self):
        # Sign with a *different* secret than the verifier knows about.
        token = _make_token(key="someone-else's-secret")
        with self.assertRaises(TokenInvalid):
            self.v.verify(token)

    def test_wrong_audience_rejected(self):
        token = _make_token(audience="some-other-audience")
        with self.assertRaises(TokenInvalid):
            self.v.verify(token)

    def test_anon_role_rejected(self):
        token = _make_token(role="anon")
        with self.assertRaises(TokenInvalid):
            self.v.verify(token)

    def test_missing_role_rejected(self):
        token = _make_token(role="")
        with self.assertRaises(TokenInvalid):
            self.v.verify(token)

    def test_missing_sub_rejected(self):
        token = _make_token(sub="")
        with self.assertRaises(TokenInvalid):
            self.v.verify(token)

    def test_empty_token_rejected(self):
        with self.assertRaises(TokenInvalid):
            self.v.verify("")

    def test_malformed_token_rejected(self):
        with self.assertRaises(TokenInvalid):
            self.v.verify("definitely.not.a.jwt")


class TestSupabaseJwtVerifierConstruction(unittest.TestCase):
    def test_both_empty_rejected_at_construction(self):
        # Only fails when BOTH means of verifying are absent. Either
        # project_url (for JWKS) OR jwt_secret (for HS256) is enough.
        with self.assertRaises(ValueError):
            SupabaseJwtVerifier(project_url="", jwt_secret="")

    def test_jwks_only_construction_accepted(self):
        # ES256-only projects only need project_url; no shared secret.
        v = SupabaseJwtVerifier(project_url=PROJECT_URL, jwt_secret="")
        self.assertIsNotNone(v)

    def test_hs256_only_construction_accepted(self):
        # Old setups may legitimately have only the secret (no project
        # URL). Verifier is fine; if an ES256 token shows up it'll
        # surface as TokenInvalid at verify time.
        v = SupabaseJwtVerifier(project_url="", jwt_secret=JWT_SECRET)
        self.assertIsNotNone(v)


# ── ES256 / JWKS verification path ─────────────────────────────────────


def _make_es256_keys():
    from cryptography.hazmat.primitives.asymmetric.ec import (
        generate_private_key, SECP256R1,
    )
    priv = generate_private_key(SECP256R1())
    return priv, priv.public_key()


class _StubJWKSClient:
    """In-process stand-in for ``jwt.PyJWKClient`` — returns a fixed key
    (or raises) without doing any HTTP.

    The verifier accepts ``jwks_client=`` directly via its constructor,
    so tests just inject this; no private attribute mucking.
    """

    def __init__(self, public_key, *, fail: bool = False):
        self._key = public_key
        self._fail = fail

    def get_signing_key_from_jwt(self, token: str):
        if self._fail:
            raise pyjwt.PyJWKClientError("kid not in JWKS")
        # ``SimpleNamespace`` lets us return something with the .key
        # attribute PyJWKClient.get_signing_key_from_jwt produces,
        # without a one-off class.
        return SimpleNamespace(key=self._key)


class TestES256Path(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.priv, cls.pub = _make_es256_keys()
        cls.other_priv, _ = _make_es256_keys()  # for tamper tests

    def _verifier(self, *, fail_jwks: bool = False) -> SupabaseJwtVerifier:
        return SupabaseJwtVerifier(
            project_url=PROJECT_URL,
            jwt_secret="",
            jwks_client=_StubJWKSClient(self.pub, fail=fail_jwks),
        )

    def _es256(self, **kwargs) -> str:
        # Thin wrapper around the unified ``_make_token`` so callers can
        # write ``self._es256(sub="alice")`` instead of repeating the
        # algorithm + key + kid header every time.
        return _make_token(
            key=self.priv,
            algorithm="ES256",
            headers={"kid": "test-key-id"},
            **kwargs,
        )

    def test_valid_es256_token_returns_user(self):
        user = self._verifier().verify(self._es256(sub="alice-es256"))
        self.assertEqual(user.user_id, "alice-es256")
        self.assertEqual(user.role, "authenticated")

    def test_expired_es256_token_raises(self):
        with self.assertRaises(TokenExpired):
            self._verifier().verify(self._es256(exp_offset=-3600))

    def test_es256_signed_with_wrong_key_rejected(self):
        # Sign with a different EC private key; verifier still has the
        # original public key → signature won't validate.
        token = _make_token(key=self.other_priv, algorithm="ES256",
                            headers={"kid": "test-key-id"})
        with self.assertRaises(TokenInvalid):
            self._verifier().verify(token)

    def test_jwks_lookup_failure_surfaces_as_token_invalid(self):
        with self.assertRaises(TokenInvalid):
            self._verifier(fail_jwks=True).verify(self._es256())

    def test_asymmetric_token_without_project_url_rejected(self):
        # HS256-only verifier sees an ES256 token → should NOT accept.
        v = SupabaseJwtVerifier(project_url="", jwt_secret=JWT_SECRET)
        with self.assertRaises(TokenInvalid):
            v.verify(self._es256())

    def test_hs256_token_against_jwks_only_verifier_rejected(self):
        # ES256-only setup (no shared secret) refuses HS256 tokens —
        # otherwise an attacker could forge tokens by signing with a
        # guessed weak secret. The verifier correctly says "I don't
        # have a key for HS256".
        v = SupabaseJwtVerifier(project_url=PROJECT_URL, jwt_secret="",
                                jwks_client=_StubJWKSClient(self.pub))
        with self.assertRaises(TokenInvalid):
            v.verify(_make_token())  # default HS256

    def test_alg_none_not_on_allow_list(self):
        # The classic JWT "alg=none" downgrade attack — making sure
        # ``none`` is not silently accepted. We assert the property at
        # the source (the allow-list) rather than reconstructing an
        # attack token, which was reaching into PyJWT internals.
        self.assertNotIn("none", SupabaseJwtVerifier.DEFAULT_ALGORITHMS)
        self.assertNotIn("NONE", SupabaseJwtVerifier.DEFAULT_ALGORITHMS)


# ── verify_jwt header parsing ─────────────────────────────────────────


class TestVerifyJwtHeader(unittest.TestCase):
    def tearDown(self) -> None:
        reset_verifier_cache()
        # Drop env vars left over from individual tests.
        for k in ("KNOWRA_DEPLOY_MODE", "SUPABASE_JWT_SECRET", "SUPABASE_PROJECT_URL"):
            os.environ.pop(k, None)

    def test_missing_header_raises_token_missing(self):
        with self.assertRaises(TokenMissing):
            verify_jwt(None)

    def test_missing_header_empty_string(self):
        with self.assertRaises(TokenMissing):
            verify_jwt("")

    def test_non_bearer_scheme_rejected(self):
        with self.assertRaises(TokenInvalid):
            verify_jwt("Basic abc")

    def test_bearer_no_token_rejected(self):
        with self.assertRaises(TokenInvalid):
            verify_jwt("Bearer")  # one part, no token

    def test_local_mode_default_accepts_any_token(self):
        # No env vars set → local mode → LocalSingleUserVerifier
        user = verify_jwt("Bearer ignored")
        self.assertEqual(user.user_id, "00000000-0000-0000-0000-000000000000")

    def test_local_mode_with_custom_user_id(self):
        os.environ["KNOWRA_LOCAL_USER_ID"] = "abc-123"
        user = verify_jwt("Bearer ignored")
        self.assertEqual(user.user_id, "abc-123")

    def test_cloud_mode_requires_env(self):
        os.environ["KNOWRA_DEPLOY_MODE"] = "cloud"
        # No SUPABASE_JWT_SECRET → get_verifier raises.
        with self.assertRaises(RuntimeError):
            verify_jwt("Bearer xyz")

    def test_cloud_mode_e2e(self):
        os.environ["KNOWRA_DEPLOY_MODE"] = "cloud"
        os.environ["SUPABASE_PROJECT_URL"] = PROJECT_URL
        os.environ["SUPABASE_JWT_SECRET"] = JWT_SECRET
        token = _make_token(sub="user-from-cloud")
        user = verify_jwt(f"Bearer {token}")
        self.assertEqual(user.user_id, "user-from-cloud")


# ── LocalSingleUserVerifier ───────────────────────────────────────────


class TestLocalSingleUserVerifier(unittest.TestCase):
    def test_returns_configured_user_regardless_of_token(self):
        v = LocalSingleUserVerifier(user_id="local-uid")
        for t in ("", "anything", "Bearer not-a-jwt"):
            self.assertEqual(v.verify(t).user_id, "local-uid")


# ── Verifier cache ─────────────────────────────────────────────────────


class TestVerifierCache(unittest.TestCase):
    def tearDown(self) -> None:
        reset_verifier_cache()

    def test_cache_returns_same_instance(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KNOWRA_DEPLOY_MODE", None)
            v1 = auth_mod.get_verifier()
            v2 = auth_mod.get_verifier()
            self.assertIs(v1, v2)

    def test_reset_invalidates_cache(self):
        v1 = auth_mod.get_verifier()
        reset_verifier_cache()
        v2 = auth_mod.get_verifier()
        self.assertIsNot(v1, v2)


# ── FastAPI dependency integration ────────────────────────────────────


class TestFastAPIDependency(unittest.TestCase):
    """Smoke-test that the ``current_user`` dependency wires into a real
    FastAPI app and translates AuthError → HTTPException correctly."""

    def setUp(self) -> None:
        # Force-import the dependency after env vars are settled.
        for k in ("KNOWRA_DEPLOY_MODE", "SUPABASE_JWT_SECRET", "SUPABASE_PROJECT_URL"):
            os.environ.pop(k, None)
        reset_verifier_cache()

    def tearDown(self) -> None:
        for k in ("KNOWRA_DEPLOY_MODE", "SUPABASE_JWT_SECRET", "SUPABASE_PROJECT_URL"):
            os.environ.pop(k, None)
        reset_verifier_cache()

    def _make_app(self):
        # Import inside the test so config.DEPLOY_MODE is recomputed per
        # env var setup.
        from fastapi import Depends, FastAPI
        from fastapi.testclient import TestClient
        from auth_deps import current_user

        app = FastAPI()

        @app.get("/whoami")
        def whoami(user=Depends(current_user)):
            return {"user_id": user.user_id, "email": user.email}

        return app, TestClient(app)

    def test_local_mode_dependency_accepts_no_header(self):
        # Default deploy mode = local
        from importlib import reload
        import config as cfg
        reload(cfg)
        app, client = self._make_app()
        resp = client.get("/whoami")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            resp.json()["user_id"],
            "00000000-0000-0000-0000-000000000000",
        )

    def test_cloud_mode_rejects_missing_header(self):
        os.environ["KNOWRA_DEPLOY_MODE"] = "cloud"
        os.environ["SUPABASE_PROJECT_URL"] = PROJECT_URL
        os.environ["SUPABASE_JWT_SECRET"] = JWT_SECRET
        from importlib import reload
        import config as cfg
        reload(cfg)
        app, client = self._make_app()
        resp = client.get("/whoami")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"]["error"], "token_missing")
        self.assertIn("WWW-Authenticate", resp.headers)

    def test_cloud_mode_accepts_valid_token(self):
        os.environ["KNOWRA_DEPLOY_MODE"] = "cloud"
        os.environ["SUPABASE_PROJECT_URL"] = PROJECT_URL
        os.environ["SUPABASE_JWT_SECRET"] = JWT_SECRET
        from importlib import reload
        import config as cfg
        reload(cfg)
        app, client = self._make_app()
        token = _make_token(sub="alice-uid", email="alice@x.com")
        resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["user_id"], "alice-uid")

    def test_cloud_mode_rejects_expired_token(self):
        os.environ["KNOWRA_DEPLOY_MODE"] = "cloud"
        os.environ["SUPABASE_PROJECT_URL"] = PROJECT_URL
        os.environ["SUPABASE_JWT_SECRET"] = JWT_SECRET
        from importlib import reload
        import config as cfg
        reload(cfg)
        app, client = self._make_app()
        token = _make_token(exp_offset=-3600)
        resp = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"]["error"], "token_expired")

    def test_cloud_mode_rejects_tampered_token(self):
        os.environ["KNOWRA_DEPLOY_MODE"] = "cloud"
        os.environ["SUPABASE_PROJECT_URL"] = PROJECT_URL
        os.environ["SUPABASE_JWT_SECRET"] = JWT_SECRET
        from importlib import reload
        import config as cfg
        reload(cfg)
        app, client = self._make_app()
        token = _make_token()
        head, payload, sig = token.split(".")
        tampered = f"{head}.{payload}.{sig[:-2]}AA"
        resp = client.get(
            "/whoami",
            headers={"Authorization": f"Bearer {tampered}"},
        )
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(resp.json()["detail"]["error"], "token_invalid")


if __name__ == "__main__":
    unittest.main()
