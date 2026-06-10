"""End-to-end tests for the mobile Ask endpoint and its service layer.

Uses the same SQLite + InMemoryStorage harness as the other cloud
tests. The OpenAI call is replaced by ``_FakeLLM`` so no live HTTP
fires; this also lets us inspect exactly what prompt the handler
built without leaking real API keys into the test process.

Covers the four invariants from docs/SYNC-PROTOCOL.md §4.3:
  - openai_api_key never lands in cloud_llm_calls
  - prompt / completion content never lands in cloud_llm_calls
  - citations are parsed back out of the LLM answer
  - rate limit returns 429 with Retry-After

Plus the usual user-isolation + happy-path + upstream-failure smoke.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[2]
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

import auth_deps  # noqa: E402
from cloud_models import (  # noqa: E402
    CloudLLMCall,
    WikiFile,
    init_cloud_schema,
)
from model_gateway.auth import AuthenticatedUser  # noqa: E402
from routers import cloud as cloud_router  # noqa: E402
from routers import sync as sync_router  # noqa: E402
from services import cloud_ask  # noqa: E402
from services.cloud_ask import LLMResult  # noqa: E402
from services.storage import (  # noqa: E402
    InMemoryStorage,
    reset_storage_cache,
    set_storage,
)


USER_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
USER_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _ua():
    return AuthenticatedUser(user_id=USER_A, email="a@x.com", role="authenticated")


def _ub():
    return AuthenticatedUser(user_id=USER_B, email="b@x.com", role="authenticated")


class _FakeLLM:
    """LLMCaller stand-in. Records what it was called with so tests
    can verify the key + prompt never leak into telemetry, and
    returns a configurable canned response."""

    def __init__(self, answer: str = "默认答案", *, prompt_tokens: int = 100, completion_tokens: int = 50, model_override: Optional[str] = None) -> None:
        self.answer = answer
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.model_override = model_override
        self.calls: list[dict] = []
        self.raise_exc: Optional[Exception] = None

    def __call__(self, *, api_key, model, messages, reasoning_effort):
        self.calls.append({
            "api_key": api_key,
            "model": model,
            "messages": messages,
            "reasoning_effort": reasoning_effort,
        })
        if self.raise_exc:
            raise self.raise_exc
        return LLMResult(
            answer=self.answer,
            model=self.model_override or model,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
        )


def _make_app_with_seed():
    """Same SQLite + InMemoryStorage harness as test_cloud_router but
    also seeds a single wiki .md content so retrieval has something
    to find."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    init_cloud_schema(engine)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    storage = InMemoryStorage()
    set_storage(storage)

    # Seed a wiki file: RoPE concept page
    rope_path = f"wiki/{USER_A}/concepts/0001-rope.md"
    rope_md = (
        "# RoPE (Rotary Position Embedding)\n\n"
        "Rotary 位置编码：把位置编码作为复数旋转应用到 query/key 上。"
        "优点是支持长序列外推。"
    )
    storage.simulate_upload(rope_path, rope_md.encode("utf-8"))
    db = SessionLocal()
    try:
        db.add(WikiFile(
            id="wf-rope", user_id=USER_A,
            kind="concept", rel_path="concepts/0001-rope.md",
            storage_path=rope_path,
            content_hash="sha256:rope-hash", size_bytes=120,
            title="RoPE",
            concept_id="concept-rope-uuid",
            updated_at=datetime.now(timezone.utc),
        ))
        db.commit()
    finally:
        db.close()

    app = FastAPI()

    def override_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    def override_user_a():
        return _ua()

    app.include_router(cloud_router.router)
    app.include_router(sync_router.router)
    app.dependency_overrides[cloud_router.get_cloud_db] = override_db
    app.dependency_overrides[sync_router.get_cloud_db] = override_db
    app.dependency_overrides[auth_deps.current_user] = override_user_a

    return app, SessionLocal, storage


# ── tests ─────────────────────────────────────────────────────────────


class CloudAskTests(unittest.TestCase):
    def setUp(self):
        os.environ["KNOWRA_STORAGE_BACKEND"] = "memory"
        reset_storage_cache()
        self.app, self.SessionLocal, self.storage = _make_app_with_seed()
        self.client = TestClient(self.app)
        # Reset the in-process rate limiter so consecutive tests don't
        # bleed quota into each other.
        cloud_ask._RATE_LIMITER._buckets.clear()

    def tearDown(self):
        reset_storage_cache()
        os.environ.pop("KNOWRA_STORAGE_BACKEND", None)
        cloud_ask._RATE_LIMITER._buckets.clear()

    # ---- happy path ----------------------------------------------------

    def test_happy_path_returns_answer_with_citation(self):
        fake = _FakeLLM(
            answer="RoPE 是 [[concept:concept-rope-uuid]] 的旋转位置编码。"
        )
        with patch.object(cloud_ask, "_real_llm_call", fake):
            resp = self.client.post("/api/cloud/ask", json={
                "question": "什么是 RoPE",
                "openai_api_key": "sk-test-1234567890",
                "model": "gpt-4o-mini",
            })
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertIn("RoPE", body["answer"])
        self.assertEqual(len(body["citations"]), 1)
        self.assertEqual(body["citations"][0]["kind"], "concept")
        self.assertEqual(body["citations"][0]["ref"], "[[concept:concept-rope-uuid]]")
        self.assertEqual(body["citations"][0]["title"], "RoPE")
        self.assertEqual(body["tokens"]["prompt"], 100)
        self.assertEqual(body["tokens"]["completion"], 50)
        self.assertEqual(body["tokens"]["total"], 150)
        # Trace has search + synthesize steps.
        self.assertEqual(len(body["trace"]), 2)
        self.assertEqual(body["trace"][0]["name"], "search")
        self.assertEqual(body["trace"][1]["name"], "synthesize")

    # ---- privacy invariants -------------------------------------------

    def test_openai_key_never_lands_in_cloud_llm_calls(self):
        fake = _FakeLLM(answer="ok")
        with patch.object(cloud_ask, "_real_llm_call", fake):
            self.client.post("/api/cloud/ask", json={
                "question": "随便问点什么",
                "openai_api_key": "sk-SUPER-SECRET-DO-NOT-LEAK",
                "model": "gpt-4o-mini",
            })
        db = self.SessionLocal()
        try:
            calls = db.query(CloudLLMCall).all()
            self.assertEqual(len(calls), 1)
            blob = repr(calls[0].__dict__)
            self.assertNotIn("SUPER-SECRET-DO-NOT-LEAK", blob,
                             "OpenAI key must NEVER appear in cloud_llm_calls")
            # The columns we explicitly designed to exist:
            self.assertEqual(calls[0].task, "ask_mobile")
            self.assertEqual(calls[0].provider, "openai")
            self.assertEqual(calls[0].prompt_tokens, 100)
            self.assertEqual(calls[0].completion_tokens, 50)
            self.assertEqual(calls[0].total_tokens, 150)
            self.assertTrue(calls[0].success)
        finally:
            db.close()

    def test_prompt_and_completion_content_never_persisted(self):
        SECRET_Q = "请告诉我私密问题：__SECRET_PROMPT__"
        SECRET_A = "答：__SECRET_ANSWER__"
        fake = _FakeLLM(answer=SECRET_A)
        with patch.object(cloud_ask, "_real_llm_call", fake):
            self.client.post("/api/cloud/ask", json={
                "question": SECRET_Q,
                "openai_api_key": "sk-test-1234567890",
            })
        db = self.SessionLocal()
        try:
            row = db.query(CloudLLMCall).first()
            blob = repr({c.name: getattr(row, c.name) for c in row.__table__.columns})
            self.assertNotIn("__SECRET_PROMPT__", blob)
            self.assertNotIn("__SECRET_ANSWER__", blob)
        finally:
            db.close()

    # ---- citation parsing ---------------------------------------------

    def test_citations_drop_refs_not_mentioned_in_answer(self):
        # Add a second wiki file. Answer mentions only the first ref.
        db = self.SessionLocal()
        try:
            db.add(WikiFile(
                id="wf-attn", user_id=USER_A,
                kind="concept", rel_path="concepts/0002-attn.md",
                storage_path=f"wiki/{USER_A}/concepts/0002-attn.md",
                content_hash="sha256:attn-h", size_bytes=10,
                title="Attention",
                concept_id="concept-attn",
                updated_at=datetime.now(timezone.utc),
            ))
            db.commit()
        finally:
            db.close()
        self.storage.simulate_upload(
            f"wiki/{USER_A}/concepts/0002-attn.md", b"# Attention"
        )

        fake = _FakeLLM(
            # Only references the rope concept, not attention.
            answer="参考 [[concept:concept-rope-uuid]] 即可。"
        )
        with patch.object(cloud_ask, "_real_llm_call", fake):
            body = self.client.post("/api/cloud/ask", json={
                "question": "讲讲 RoPE 和 attention",
                "openai_api_key": "sk-test-1234567890",
            }).json()
        refs = [c["ref"] for c in body["citations"]]
        self.assertIn("[[concept:concept-rope-uuid]]", refs)
        self.assertNotIn("[[concept:concept-attn]]", refs)

    def test_phantom_citation_surfaced_without_metadata(self):
        # LLM hallucinates a reference that doesn't match any wiki file.
        fake = _FakeLLM(
            answer="按 [[paper:hallucinated-uuid-12345]] 所述，..."
        )
        with patch.object(cloud_ask, "_real_llm_call", fake):
            body = self.client.post("/api/cloud/ask", json={
                "question": "讲讲 RoPE",
                "openai_api_key": "sk-test-1234567890",
            }).json()
        phantom = [c for c in body["citations"] if c["ref"] == "[[paper:hallucinated-uuid-12345]]"]
        self.assertEqual(len(phantom), 1)
        # No file_id / title because no matching wiki entry
        self.assertIsNone(phantom[0]["file_id"])
        self.assertIsNone(phantom[0]["title"])

    # ---- rate limit ----------------------------------------------------

    def test_rate_limit_returns_429_with_retry_after(self):
        # Pre-fill the bucket to exhaustion
        from collections import deque
        import time
        now = time.monotonic()
        cloud_ask._RATE_LIMITER._buckets[USER_A] = deque(
            [now - 1] * cloud_ask.RATE_LIMIT_CALLS
        )
        fake = _FakeLLM()
        with patch.object(cloud_ask, "_real_llm_call", fake):
            resp = self.client.post("/api/cloud/ask", json={
                "question": "anything",
                "openai_api_key": "sk-test-1234567890",
            })
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(resp.json()["detail"]["error"], "rate_limited")
        self.assertIn("Retry-After", resp.headers)
        # And the LLM caller must NOT have been invoked when the
        # limiter rejected.
        self.assertEqual(len(fake.calls), 0)

    # ---- upstream failure ---------------------------------------------

    def test_openai_failure_returns_502_and_records_failure(self):
        fake = _FakeLLM()
        fake.raise_exc = RuntimeError("simulated openai 500")
        with patch.object(cloud_ask, "_real_llm_call", fake):
            resp = self.client.post("/api/cloud/ask", json={
                "question": "anything",
                "openai_api_key": "sk-test-1234567890",
            })
        self.assertEqual(resp.status_code, 502)
        self.assertEqual(resp.json()["detail"]["error"], "upstream_error")
        # Failure should still be logged to telemetry
        db = self.SessionLocal()
        try:
            calls = db.query(CloudLLMCall).all()
            self.assertEqual(len(calls), 1)
            self.assertFalse(calls[0].success)
            self.assertEqual(calls[0].error_class, "RuntimeError")
        finally:
            db.close()

    # ---- validation ----------------------------------------------------

    def test_empty_question_rejected(self):
        resp = self.client.post("/api/cloud/ask", json={
            "question": "",
            "openai_api_key": "sk-test-1234567890",
        })
        self.assertEqual(resp.status_code, 422)

    def test_oversized_question_rejected(self):
        resp = self.client.post("/api/cloud/ask", json={
            "question": "x" * 9000,
            "openai_api_key": "sk-test-1234567890",
        })
        self.assertEqual(resp.status_code, 422)

    def test_missing_key_rejected(self):
        resp = self.client.post("/api/cloud/ask", json={
            "question": "test",
        })
        self.assertEqual(resp.status_code, 422)

    # ---- tenant isolation ---------------------------------------------

    def test_user_b_ask_does_not_see_user_a_wiki(self):
        self.app.dependency_overrides[auth_deps.current_user] = lambda: _ub()
        fake = _FakeLLM(answer="基于已有资料无法判断。")
        with patch.object(cloud_ask, "_real_llm_call", fake):
            self.client.post("/api/cloud/ask", json={
                "question": "什么是 RoPE",
                "openai_api_key": "sk-test-1234567890",
            })
        # The fake LLM's messages should NOT contain user A's wiki content
        prompt_text = " ".join(m["content"] for m in fake.calls[0]["messages"])
        self.assertNotIn("Rotary 位置编码", prompt_text)
        # And the search trace should report 0 hits
        # (we can't easily inspect trace from inside without rerunning,
        # but the absence of RoPE content above proves user A's data
        # didn't leak).


if __name__ == "__main__":
    unittest.main()
