"""LLM-based promotion stage.

Heuristics (services.promotion_service) decide the obvious cases. Whatever
they leave as "ambiguous-deferred" (status=pending with a recent
last_promotion_eval_at) ends up here, where we ask the LLM to judge each
candidate against the corpus it's cited in.

The LLM is asked for a strict JSON array — one verdict per candidate —
and we parse defensively (fall back to leaving the node pending on parse
failure rather than randomly bouncing it between promoted/rejected).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import List, Optional

from sqlalchemy.orm import Session

from config import load_config, task_model_id
from models import KnowledgeNode, Paper
from services.graph_service import (
    PROMOTED_BY_LLM,
    PROMOTION_PENDING,
    PROMOTION_PROMOTED,
    PROMOTION_REJECTED,
    is_concept_candidate_node,
    normalize_source_paper_ids,
    promotion_status,
)
from services.promotion_service import (
    RE_EVAL_AFTER,
    apply_decision,
    PromotionDecision,
    _now,
    _to_aware,
)
from services.wiki_compiler import _call_llm, _snippet_for_paper

log = logging.getLogger("promotion_llm")

LLM_BATCH_SIZE = 15
MAX_SNIPPET_CHARS = 600  # per-paper context, kept tight to stay in budget

# Re-export for back-compat. Canonical home is `prompts.py` so config.py
# can use it as the first-run default without a circular import.
from prompts import DEFAULT_PROMOTION_PROMPT as PROMOTION_LLM_DEFAULT_PROMPT

# JSON-output instructions appended to whatever custom prompt the user
# configures, so the parser stays reliable even when users write a
# free-form prompt that forgets to specify the schema.
PROMOTION_LLM_OUTPUT_CONTRACT = (
    "\n\n[输出协议 — 不要修改]\n"
    "严格输出 JSON 数组，每条 "
    "{\"id\": <int>, \"decision\": \"promote\"|\"reject\", \"reason\": <一句话中文理由>}；"
    "不要 markdown 代码块，不要多余文字。"
)


class PromotionLLMUnavailable(Exception):
    """Raised when LLM stage cannot run (missing API key, etc.)."""


@dataclass
class LLMRunResult:
    promoted: int
    rejected: int
    still_ambiguous: int  # parse-failed or response missed them
    total_evaluated: int
    model: str


def _candidates_for_llm(db: Session) -> list[KnowledgeNode]:
    """The LLM stage processes nodes that the heuristic has *seen* (so
    `last_promotion_eval_at` is set) but left as `pending`. User-pinned
    nodes are excluded."""
    cutoff = _now() - RE_EVAL_AFTER
    out: list[KnowledgeNode] = []
    for node in db.query(KnowledgeNode).all():
        if not is_concept_candidate_node(node):
            continue
        if (node.promoted_by or "").lower() == "user":
            continue
        if promotion_status(node) != PROMOTION_PENDING:
            continue
        last_eval = _to_aware(getattr(node, "last_promotion_eval_at", None))
        if last_eval is None or last_eval < cutoff:
            # Heuristic hasn't visited this one yet; let it run first so the
            # LLM only deals with truly ambiguous nodes.
            continue
        out.append(node)
    return out


def _build_candidate_block(node: KnowledgeNode, papers_by_id: dict[str, Paper]) -> dict:
    paper_ids = normalize_source_paper_ids(node.source_paper_ids)
    snippets = []
    for pid in paper_ids:
        paper = papers_by_id.get(pid)
        if paper is None:
            continue
        snippet = _snippet_for_paper(paper)[:MAX_SNIPPET_CHARS]
        snippets.append({"paper_id": pid, "title": paper.title, "snippet": snippet})
    return {
        "id": node.id,
        "title": node.title,
        "node_type": node.node_type,
        "tags": list(node.tags or [])[:10],
        "papers": snippets,
    }


def _user_prompt(batch: list[dict]) -> str:
    return (
        "请评审以下候选概念列表，对每个返回 promote / reject 决策。\n\n"
        + json.dumps(batch, ensure_ascii=False, indent=2)
    )


def _parse_decisions(raw: str) -> dict[str, dict]:
    """Tolerantly parse the LLM's JSON. Strips code fences and falls back to
    finding the first `[` ... `]` block if the response wraps the array in
    extra prose.

    Keyed by ``str(id)`` — node ids are UUID strings post-migration (and
    were INT pre-migration). The previous ``int(id)`` cast silently
    dropped EVERY decision once ids became UUIDs, leaving all candidates
    'still ambiguous' (the 自动剔除 no-op bug)."""
    text = (raw or "").strip()
    for fence in ("```json", "```"):
        if text.startswith(fence):
            text = text[len(fence):].strip()
        if text.endswith("```"):
            text = text[: -3].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return {}
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    if not isinstance(data, list):
        return {}
    out: dict[str, dict] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        # IDs are opaque strings (UUID now, INT before migration). The
        # LLM echoes back whatever we sent in the candidate block's
        # "id" field, so compare as strings.
        node_id = str(entry.get("id") or "").strip()
        if not node_id:
            continue
        decision = (entry.get("decision") or "").strip().lower()
        if decision not in {"promote", "reject"}:
            continue
        out[node_id] = {
            "decision": decision,
            "reason": (entry.get("reason") or "").strip()[:500],
        }
    return out


def run_llm_pass(db: Session) -> LLMRunResult:
    cfg = load_config()
    user_prompt_template = (cfg.get("promotion_prompt") or "").strip()
    if not user_prompt_template:
        raise PromotionLLMUnavailable(
            "未配置剔除提示词 — 跳过 Agent，本次只跑启发式"
        )
    system_prompt = user_prompt_template + PROMOTION_LLM_OUTPUT_CONTRACT
    model = task_model_id(cfg, "promotion_judge")

    candidates = _candidates_for_llm(db)
    if not candidates:
        return LLMRunResult(0, 0, 0, 0, model)

    paper_ids: set[str] = set()
    for node in candidates:
        paper_ids.update(normalize_source_paper_ids(node.source_paper_ids))
    papers = db.query(Paper).filter(Paper.id.in_(list(paper_ids))).all() if paper_ids else []
    papers_by_id = {p.id: p for p in papers}

    promoted = rejected = still_ambiguous = 0
    now = _now()

    for batch_start in range(0, len(candidates), LLM_BATCH_SIZE):
        batch_nodes = candidates[batch_start : batch_start + LLM_BATCH_SIZE]
        batch_payload = [_build_candidate_block(n, papers_by_id) for n in batch_nodes]
        try:
            raw = _call_llm(
                None,
                model,
                system_prompt,
                _user_prompt(batch_payload),
                max_tokens=1500,
                task_id="promotion_judge",
            )
        except Exception as exc:
            log.warning("LLM batch failed (%s); leaving %d nodes pending", exc, len(batch_nodes))
            still_ambiguous += len(batch_nodes)
            continue

        decisions = _parse_decisions(raw)
        for node in batch_nodes:
            verdict = decisions.get(str(node.id))
            if not verdict:
                still_ambiguous += 1
                continue
            if verdict["decision"] == "promote":
                apply_decision(
                    node,
                    PromotionDecision(PROMOTION_PROMOTED, PROMOTED_BY_LLM, verdict["reason"]),
                    when=now,
                )
                promoted += 1
            else:
                apply_decision(
                    node,
                    PromotionDecision(PROMOTION_REJECTED, PROMOTED_BY_LLM, verdict["reason"]),
                    when=now,
                )
                rejected += 1

    db.commit()
    return LLMRunResult(
        promoted=promoted,
        rejected=rejected,
        still_ambiguous=still_ambiguous,
        total_evaluated=len(candidates),
        model=model,
    )
