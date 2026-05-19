"""P1 — Wiki content linter / health-check agent.

Karpathy's blueprint §5: run LLM health checks to find inconsistent data,
spot merge candidates, surface cross-cutting article candidates, and
suggest follow-up questions worth investigating.

Knowra already has *structural* freshness (compile-signature diff) and a
promotion lifecycle. This module adds the missing *content* layer.

Design — bounded cost:
  - A cheap rule layer does the heavy scoping in pure Python:
      * stub detection      (short body / single source paper)
      * merge candidates    (concept embedding cosine, paper overlap)
      * missing cross-cut   (paper clusters with no spanning concept)
  - ONE structured LLM call then *judges* the pre-scoped material and
    also produces follow-up questions. So token cost is ~1 call
    regardless of wiki size.

Output: a JSON payload (for the UI) AND a durable `data/wiki/lint-report.md`
the user reviews & acts on — same "review the report, then apply" loop
Karpathy describes.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from config import load_config, task_model_id
from models import KnowledgeNode, Paper
from services.vlm_service import cosine_similarity
from services.wiki_compiler import (
    WIKI_DIR,
    _call_llm,
    _dedup_aliases,
    _render_frontmatter,
    list_publishable_concept_nodes,
    read_concept_page,
)

LINT_REPORT_PATH = WIKI_DIR / "lint-report.md"

# --- rule thresholds ----------------------------------------------------

STUB_BODY_CHARS = 360       # body shorter than this == likely a stub
STUB_MIN_SOURCE_PAPERS = 2  # cited by < this many papers == thin
MERGE_COSINE_MIN = 0.86     # concept-embedding similarity to flag a pair
MERGE_TOPK = 24             # cap pairs sent to the LLM
CROSSCUT_MIN_CLUSTER = 3    # papers sharing a tag with no spanning concept
CROSSCUT_TOPK = 12
FOLLOWUP_COUNT = 5


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _word_len(text: str) -> int:
    # Mixed CJK/latin: count CJK chars individually + latin words.
    cjk = len(re.findall(r"[一-鿿]", text or ""))
    latin = len(re.findall(r"[A-Za-z0-9]+", text or ""))
    return cjk + latin


# --- rule layer ---------------------------------------------------------


def _scan_stubs(concept_nodes: list[KnowledgeNode]) -> list[dict]:
    """Pure-rule stub flags. The LLM later refines (thin vs redundant vs
    fine-as-is) but the rule scopes which pages even get looked at."""
    out: list[dict] = []
    for node in concept_nodes:
        page = None
        # Concept .md filename isn't 1:1 derivable cheaply; scan by id.
        # read_concept_page needs a filename, so resolve via the slug the
        # compiler uses: {id:04d}-{slug}. Fall back to None gracefully.
        from services.wiki_compiler import _concept_page_path

        path = _concept_page_path(node)
        if path.is_file():
            page = read_concept_page(path.name)
        body = (page or {}).get("body", "") if page else ""
        n_papers = len(set(node.source_paper_ids or []))
        wl = _word_len(body)

        # "待充实" must mean a real content deficiency that recompiling
        # can actually fix: either no .md exists yet, or the body is
        # genuinely thin. A concept being cited by only 1 paper is NOT a
        # deficiency — it's normal for niche concepts in a personal
        # library, and recompiling re-runs the LLM on the same single
        # source so the page barely changes. Single-paper count is kept
        # only as supplementary context on items already flagged for a
        # content reason; it never triggers on its own.
        missing_page = not page
        thin_body = bool(wl) and wl < STUB_BODY_CHARS
        is_stub = missing_page or thin_body
        if not is_stub:
            continue

        reasons = []
        if missing_page:
            reasons.append("无对应 .md（未编译）")
        if thin_body:
            reasons.append(f"正文偏短（约 {wl} 字）")
        if n_papers < STUB_MIN_SOURCE_PAPERS:
            # Context only — explains *why* the page is thin, but isn't
            # itself the trigger.
            reasons.append(f"（仅 {n_papers} 篇论文引用）")

        out.append(
            {
                "concept_id": node.id,
                "title": node.title,
                "node_type": node.node_type,
                "source_paper_count": n_papers,
                "body_word_len": wl,
                "filename": path.name if path else None,
                "reasons": reasons,
                "excerpt": (body or node.content or "")[:200],
            }
        )
    # Worst offenders first.
    out.sort(key=lambda x: (x["source_paper_count"], x["body_word_len"]))
    return out


def _scan_merge_candidates(concept_nodes: list[KnowledgeNode]) -> list[dict]:
    """Concept pairs with high embedding cosine; paper overlap boosts the
    score (two concepts citing the same papers are stronger merge bets)."""
    embedded = [n for n in concept_nodes if isinstance(n.embedding, list) and n.embedding]
    pairs: list[dict] = []
    for a, b in combinations(embedded, 2):
        try:
            sim = cosine_similarity(a.embedding, b.embedding)
        except Exception:
            continue
        if sim < MERGE_COSINE_MIN:
            continue
        sa = set(a.source_paper_ids or [])
        sb = set(b.source_paper_ids or [])
        overlap = len(sa & sb)
        union = len(sa | sb) or 1
        jaccard = overlap / union
        pairs.append(
            {
                "a_id": a.id,
                "a_title": a.title,
                "b_id": b.id,
                "b_title": b.title,
                "cosine": round(float(sim), 4),
                "paper_overlap": overlap,
                "paper_jaccard": round(jaccard, 3),
                "score": round(float(sim) + 0.15 * jaccard, 4),
            }
        )
    pairs.sort(key=lambda p: p["score"], reverse=True)
    return pairs[:MERGE_TOPK]


def _scan_missing_crosscut(
    db: Session, concept_nodes: list[KnowledgeNode]
) -> list[dict]:
    """Find paper clusters that recur together across concept membership
    but have no single concept node spanning them — candidate new
    cross-cutting articles."""
    # Map paper_id -> set(concept_ids) it appears under.
    paper_to_concepts: dict[int, set[int]] = defaultdict(set)
    concept_paper_sets: dict[int, set[int]] = {}
    for n in concept_nodes:
        ps = set(n.source_paper_ids or [])
        concept_paper_sets[n.id] = ps
        for pid in ps:
            paper_to_concepts[pid].add(n.id)

    # Co-occurrence: count how often each unordered paper pair shares a
    # concept; build clusters from frequently-co-occurring pairs.
    co: dict[tuple[int, int], int] = defaultdict(int)
    for ps in concept_paper_sets.values():
        for x, y in combinations(sorted(ps), 2):
            co[(x, y)] += 1

    # Greedy cluster seeds from the strongest co-occurring pairs.
    strong_pairs = sorted(
        ((k, v) for k, v in co.items() if v >= 2), key=lambda kv: kv[1], reverse=True
    )
    seen_clusters: list[set[int]] = []
    for (x, y), _w in strong_pairs:
        merged = None
        for cl in seen_clusters:
            if x in cl or y in cl:
                cl.add(x)
                cl.add(y)
                merged = cl
                break
        if merged is None:
            seen_clusters.append({x, y})

    titles = {
        p.id: p.title
        for p in db.query(Paper).filter(
            Paper.id.in_([pid for cl in seen_clusters for pid in cl] or [0])
        ).all()
    }

    out: list[dict] = []
    for cl in seen_clusters:
        if len(cl) < CROSSCUT_MIN_CLUSTER:
            continue
        # Is there already a concept whose source set covers most of cl?
        covered = any(
            len(cl & ps) >= max(2, int(0.7 * len(cl)))
            for ps in concept_paper_sets.values()
        )
        if covered:
            continue
        out.append(
            {
                "paper_ids": sorted(cl),
                "paper_titles": [titles.get(pid, f"paper-{pid}") for pid in sorted(cl)],
                "size": len(cl),
            }
        )
    out.sort(key=lambda c: c["size"], reverse=True)
    return out[:CROSSCUT_TOPK]


# --- LLM judgment (single structured call) ------------------------------

LINT_SYSTEM = (
    "你是个人 LLM 知识库的健康检查助手。系统已经用规则预筛了候选，"
    "你只需基于给定材料做判断，禁止编造库里没有的内容。\n"
    "严格输出 JSON，结构：\n"
    "{\n"
    '  "stubs": [{"concept_id": <int>, "verdict": "enrich"|"merge"|"drop"|"ok",'
    ' "reason": <一句话中文>}],\n'
    '  "merges": [{"a_id": <int>, "b_id": <int>, "should_merge": <bool>,'
    ' "keep": <int|null>, "reason": <一句话中文>}],\n'
    '  "new_concepts": [{"title": <string>, "paper_ids": [<int>...],'
    ' "rationale": <一句话中文>}],\n'
    '  "followups": [<string>... ]\n'
    "}\n"
    "不要 markdown 代码围栏，不要多余文字。followups 给"
    f" {FOLLOWUP_COUNT} 个值得继续深挖的高价值问题。"
)


def _strip_fence(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        nl = t.find("\n")
        if nl != -1:
            t = t[nl + 1 :]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def _llm_judge(
    *,
    stubs: list[dict],
    merges: list[dict],
    crosscut: list[dict],
) -> dict[str, Any]:
    cfg = load_config()
    model = task_model_id(cfg, "wiki_lint")
    # Keep the prompt lean — a slow local Codex CLI times out on big
    # blobs. The LLM only needs titles + reasons to judge enrich/merge/
    # drop; excerpts and the full index are dropped. Tighter caps than
    # the rule-layer TOPK so the call stays well under the timeout.
    payload = {
        "stub_candidates": [
            {
                "concept_id": s["concept_id"],
                "title": s["title"],
                "reasons": s["reasons"],
            }
            for s in stubs[:20]
        ],
        "merge_candidates": [
            {
                "a_id": m["a_id"],
                "a_title": m["a_title"],
                "b_id": m["b_id"],
                "b_title": m["b_title"],
                "cosine": m["cosine"],
                "paper_overlap": m["paper_overlap"],
            }
            for m in merges[:12]
        ],
        "paper_clusters_without_concept": [
            {
                "paper_ids": c["paper_ids"][:8],
                "paper_titles": c["paper_titles"][:5],
            }
            for c in crosscut[:6]
        ],
    }
    user = (
        "以下是规则预筛后的材料，请按 system 约定的 JSON 输出判断。"
        "followups 可结合 stub/merge/cluster 的主题来提：\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    try:
        # Offline batch op — generous ceiling so a slow local model
        # doesn't fall back to rule-only just for being slow.
        raw = _call_llm(
            None,
            model,
            LINT_SYSTEM,
            user,
            max_tokens=2200,
            task_id="wiki_lint",
            timeout_s=600,
        )
        parsed = json.loads(_strip_fence(raw))
        if isinstance(parsed, dict):
            return {"used_model": True, "model": model, **parsed}
    except Exception as exc:  # noqa: BLE001 — degrade, never crash a lint run
        return {"used_model": False, "model": model, "error": str(exc)}
    return {"used_model": False, "model": model, "error": "unparseable LLM output"}


# --- report writer ------------------------------------------------------


def _render_report(result: dict[str, Any]) -> str:
    meta = {
        "kind": "lint",
        "title": "Wiki 健康检查报告",
        "aliases": _dedup_aliases(["lint-report", "Wiki 健康检查报告", "lint"]),
        "generated_at": result["generated_at"],
        "model": result.get("judgment", {}).get("model"),
        "counts": json.dumps(result["counts"], ensure_ascii=False),
    }
    lines = [_render_frontmatter(meta), "# Wiki 健康检查报告", ""]
    c = result["counts"]
    lines.append(
        f"> 待充实 {c['stubs']} · 可合并对 {c['merges']} · 待建概念 {c['missing_crosscut']} · 追问 {c['followups']}"
    )
    lines.append("")

    j = result.get("judgment", {})

    lines.append("## 待充实条目")
    verdict_by_id = {s["concept_id"]: s for s in j.get("stubs", []) if isinstance(s, dict)}
    if result["stubs"]:
        for s in result["stubs"]:
            v = verdict_by_id.get(s["concept_id"], {})
            verdict = v.get("verdict", "")
            reason = v.get("reason", "")
            tail = f" — **{verdict}**：{reason}" if verdict else ""
            link = f"[[concept:{s['concept_id']}]]"
            lines.append(
                f"- {link} {s['title']}（{' / '.join(s['reasons'])}）{tail}"
            )
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 可合并概念对")
    if result["merges"]:
        mj = {(m.get("a_id"), m.get("b_id")): m for m in j.get("merges", []) if isinstance(m, dict)}
        for m in result["merges"]:
            jm = mj.get((m["a_id"], m["b_id"])) or {}
            should = jm.get("should_merge")
            mark = "✅ 建议合并" if should else ("➖ 暂不合并" if should is False else "")
            keep = jm.get("keep")
            keep_txt = f"，保留 #{keep}" if keep else ""
            reason = jm.get("reason", "")
            lines.append(
                f"- [[concept:{m['a_id']}]] {m['a_title']} ⇆ "
                f"[[concept:{m['b_id']}]] {m['b_title']} "
                f"(cos={m['cosine']}, 共享 {m['paper_overlap']} 篇) {mark}{keep_txt} {reason}".rstrip()
            )
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 建议新建的概念（串联多篇论文）")
    new_concepts = [n for n in j.get("new_concepts", []) if isinstance(n, dict)]
    if new_concepts:
        for n in new_concepts:
            pids = ", ".join(f"[[paper:{p}]]" for p in (n.get("paper_ids") or []))
            lines.append(f"- **{n.get('title', '?')}** — {n.get('rationale', '')}  \n  覆盖：{pids}")
    elif result["missing_crosscut"]:
        for c2 in result["missing_crosscut"]:
            pids = ", ".join(f"[[paper:{p}]]" for p in c2["paper_ids"])
            lines.append(f"- （{c2['size']} 篇论文簇，未串联）覆盖：{pids}")
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 建议接着研究的问题")
    followups = [q for q in j.get("followups", []) if isinstance(q, str)]
    if followups:
        for q in followups:
            lines.append(f"- {q}")
    else:
        lines.append("- （本次未生成）")
    lines.append("")

    return "\n".join(lines)


# --- public entry -------------------------------------------------------


def run_lint(db: Session, *, use_llm: bool = True) -> dict[str, Any]:
    concept_nodes = list_publishable_concept_nodes(db)

    stubs = _scan_stubs(concept_nodes)
    merges = _scan_merge_candidates(concept_nodes)
    crosscut = _scan_missing_crosscut(db, concept_nodes)

    judgment: dict[str, Any] = {"used_model": False}
    if use_llm and (stubs or merges or crosscut):
        judgment = _llm_judge(
            stubs=stubs,
            merges=merges,
            crosscut=crosscut,
        )

    result = {
        "generated_at": _now_iso(),
        "counts": {
            "concepts_scanned": len(concept_nodes),
            "stubs": len(stubs),
            "merges": len(merges),
            "missing_crosscut": len(crosscut),
            "followups": len(
                [q for q in judgment.get("followups", []) if isinstance(q, str)]
            ),
        },
        "stubs": stubs,
        "merges": merges,
        "missing_crosscut": crosscut,
        "judgment": judgment,
    }

    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    LINT_REPORT_PATH.write_text(_render_report(result), encoding="utf-8")
    result["report_path"] = str(LINT_REPORT_PATH)
    result["report_rel_path"] = "data/wiki/lint-report.md"
    return result


def lint_report_status() -> dict:
    if not LINT_REPORT_PATH.is_file():
        return {"exists": False}
    st = LINT_REPORT_PATH.stat()
    return {
        "exists": True,
        "rel_path": "data/wiki/lint-report.md",
        "size": st.st_size,
        "modified_at": datetime.fromtimestamp(
            st.st_mtime, tz=timezone.utc
        ).isoformat(),
    }


def read_lint_report() -> Optional[str]:
    if not LINT_REPORT_PATH.is_file():
        return None
    try:
        return LINT_REPORT_PATH.read_text(encoding="utf-8")
    except OSError:
        return None
