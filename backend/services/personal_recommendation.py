"""Metadata-only ranking. Pure functions are shared by server and CLI worker."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

DIMENSIONS = ("domain", "problem", "method", "dataset", "team")
STOP = {
    "a",
    "an",
    "the",
    "of",
    "and",
    "or",
    "for",
    "with",
    "in",
    "on",
    "to",
    "from",
    "by",
    "is",
    "are",
    "this",
    "that",
    "using",
    "via",
    "based",
    "learning",
    "model",
    "models",
    "paper",
    "approach",
    "new",
    "we",
    "our",
    "it",
    "as",
    "at",
    "be",
    "can",
    "its",
    "into",
    "through",
    "has",
    "have",
}
ARXIV = re.compile(
    r"(?<!\d)(\d{4}\.\d{4,5}|[a-z][a-z.\-]+[/_]\d{7})(?:v\d+)?", re.IGNORECASE
)


def utcnow():
    return datetime.now(timezone.utc)


def aware(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return value.replace(tzinfo=timezone.utc) if value and not value.tzinfo else value


def base_id(value):
    match = ARXIV.search(str(value or ""))
    return match.group(1).replace("_", "/").lower() if match else None


def terms(text):
    return {
        t
        for t in re.findall(
            r"[a-z][a-z0-9-]{2,}|[\u4e00-\u9fff]{2,8}", str(text).lower()
        )
        if t not in STOP
    }


def signature(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def normalized_title(value):
    return re.sub(r"\W+", "", str(value or "").casefold())


def _labels(value):
    if isinstance(value, str):
        return [value[:160]] if value.strip() else []
    if isinstance(value, list):
        return [
            v[:160]
            if isinstance(v, str)
            else str(v.get("name", v.get("title", "")))[:160]
            for v in value[:30]
            if isinstance(v, (str, dict))
        ]
    return []


def paper_features(paper, nodes=()):
    """Reuse existing extraction fields, never full text/notes/chat history."""
    features = {d: [] for d in DIMENSIONS}
    features["domain"] = _labels(
        paper.get("paper_category_override") or paper.get("paper_category_model")
    )
    features["team"] = _labels(
        paper.get("paper_team_override") or paper.get("paper_team_model")
    )
    try:
        raw = paper.get("raw_llm_response") or "{}"
        raw = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(raw, dict):
            raw = {}
    except (ValueError, TypeError):
        raw = {}
    for dimension, keys in {
        "domain": ("tags",),
        "method": ("techniques", "methods"),
        "dataset": ("datasets",),
        "problem": ("problem", "research_problem"),
    }.items():
        for key in keys:
            features[dimension].extend(_labels(raw.get(key)))
    for node in nodes:
        dimension = {
            "technique": "method",
            "dataset": "dataset",
            "problem": "problem",
        }.get(node.get("node_type"))
        if dimension:
            features[dimension].extend(_labels(node.get("title")))
    return {
        d: sorted(
            {
                v.strip().casefold()
                for v in values
                if v.strip() and v.casefold() not in {"others", "其他"}
            }
        )[:40]
        for d, values in features.items()
    }


def build_profile(papers, nodes=(), focus="", feedback=None, now=None):
    now = now or utcnow()
    counts, recent, evidence, teams = Counter(), Counter(), {}, {}
    dimensions = {d: Counter() for d in DIMENSIONS}
    library_ids, library_titles = set(), set()
    for paper in sorted(papers, key=lambda p: str(p.get("id", ""))):
        aid = base_id(paper.get("filename")) or base_id(paper.get("arxiv_id"))
        if aid:
            library_ids.add(aid)
        title = paper.get("title") or paper.get("filename") or ""
        if normalized_title(title):
            library_titles.add(normalized_title(title))
        related = [
            n
            for n in nodes
            if str(paper.get("id"))
            in [str(x) for x in (n.get("source_paper_ids") or [])]
        ]
        features = paper_features(paper, related)
        words = terms(
            title + " " + " ".join(v for values in features.values() for v in values)
        )
        # Each paper contributes one unit regardless of label/node count.
        for word in sorted(words):
            counts[word] += 1 / max(len(words), 1)
            evidence.setdefault(word, [])
            if len(evidence[word]) < 3:
                evidence[word].append(
                    {"id": str(paper.get("id")), "title": title[:300]}
                )
        if aware(paper.get("created_at")) and aware(
            paper["created_at"]
        ) >= now - timedelta(days=30):
            for word in sorted(words):
                recent[word] += 1 / max(len(words), 1)
        for dimension, values in features.items():
            for value in values:
                dimensions[dimension][value] += 1 / max(len(values), 1)
        for author in paper.get("authors") or []:
            if isinstance(author, str) and features["team"]:
                teams.setdefault(author.casefold(), set()).update(features["team"])
    if focus.strip():
        focus_words = terms(focus)
        recent = Counter({t: 1 / max(1, len(focus_words)) for t in sorted(focus_words)})
    return {
        "paper_count": len(papers),
        "focus": focus,
        "long_term": dict(counts.most_common(250)),
        "recent": dict(recent.most_common(100)),
        "feedback": feedback or {},
        "dimensions": {d: dict(c.most_common(25)) for d, c in dimensions.items()},
        "evidence": {k: evidence[k] for k in dict(counts.most_common(250))},
        "teams": {a: sorted(v) for a, v in teams.items()},
        "library_ids": sorted(library_ids),
        "library_titles": sorted(library_titles),
        "algorithm": "content-positive-v1",
    }


def query_plan(profile):
    def queries(weights, limit):
        words = [
            t
            for t, _ in sorted(weights.items(), key=lambda x: (-x[1], x[0]))
            if re.fullmatch(r"[a-z][a-z0-9-]{2,}", t)
        ]
        return [f'(ti:"{word}" OR abs:"{word}")' for word in words[:limit]]

    # Feedback also affects retrieval; keep the long-term prior dominant.
    weights = Counter(profile.get("long_term", {}))
    weights.update({k: v * 0.2 for k, v in profile.get("feedback", {}).items()})
    result = queries(weights, 4) + queries(profile.get("recent", {}), 2)
    return list(dict.fromkeys(result))[:6]


def _similarity(weights, words):
    if not weights or not words:
        return 0.0
    return sum(weights.get(w, 0) for w in words) / (
        math.sqrt(sum(v * v for v in weights.values())) * math.sqrt(len(words))
    )


def rank_candidates(profile, candidates, *, excluded=(), now=None):
    now = now or utcnow()
    excluded = set(excluded) | set(profile.get("library_ids", []))
    ranked, seen = [], set()
    for candidate in candidates:
        aid = base_id(candidate.get("arxiv_id"))
        if (
            not aid
            or aid in excluded
            or aid in seen
            or normalized_title(candidate.get("title"))
            in profile.get("library_titles", [])
        ):
            continue
        seen.add(aid)
        metadata = {
            k: candidate.get(k)
            for k in ("title", "abstract", "authors", "primary_category", "published")
        }
        metadata["title"] = str(metadata["title"] or "")[:500]
        metadata["abstract"] = str(metadata["abstract"] or "")[:12000]
        metadata["authors"] = [
            a[:200] for a in (metadata["authors"] or [])[:80] if isinstance(a, str)
        ]
        features = candidate.get("features") or {}
        team_matches = sorted(
            {
                t
                for a in metadata["authors"]
                for t in profile.get("teams", {}).get(a.casefold(), [])
            }
        )
        words = terms(
            metadata["title"]
            + " "
            + metadata["abstract"]
            + " "
            + " ".join(team_matches)
        )
        # Cached AI labels describe the paper but never replace primary text evidence.
        long_score = _similarity(profile.get("long_term", {}), words)
        short_score = _similarity(profile.get("recent", {}), words)
        positive = _similarity(profile.get("feedback", {}), words)
        relevance = 0.7 * long_score + 0.2 * short_score + 0.1 * positive
        if team_matches:
            relevance += 0.04
        if relevance < 0.025:
            continue
        published = aware(metadata.get("published"))
        historical = bool(published and published < now - timedelta(days=30))
        matches = sorted(
            words & profile.get("long_term", {}).keys(),
            key=lambda t: -profile["long_term"][t],
        )[:6]
        sources = {}
        for word in matches:
            for source in profile.get("evidence", {}).get(word, []):
                sources[source["id"]] = source
        ranked.append(
            {
                **metadata,
                "arxiv_id": aid,
                "pdf_url": f"https://arxiv.org/pdf/{aid}",
                "score": round(relevance + (0 if historical else 0.015), 6),
                "base_score": round(relevance, 6),
                "historical": historical,
                "matched_terms": matches,
                "matched_teams": team_matches,
                "sources": list(sources.values())[:3],
                "features": features,
                "lane": "recent"
                if short_score > long_score and short_score > 0.04
                else "long_term",
                "reason": "匹配你的知识库：" + "、".join(matches or team_matches),
                "ai": False,
            }
        )
    return sorted(ranked, key=lambda c: (-c["score"], c["arxiv_id"]))


def select_diverse(ranked, limit=10):
    """Quality-gated quotas; never fill quotas with irrelevant papers."""
    chosen, titles = [], set()
    teams = Counter()
    historical = 0
    # Reserve one slot for a relevant adjacent item outside the top head.
    recent = [c for c in ranked if c["lane"] == "recent"][:2]
    ordered = [c for c in ranked if c["lane"] == "long_term"][:7] + recent
    selected_ids = {c["arxiv_id"] for c in ordered}
    exploration = next(
        (
            c
            for c in ranked
            if c["arxiv_id"] not in selected_ids and c["base_score"] >= 0.035
        ),
        None,
    )
    if exploration:
        ordered.append({**exploration, "lane": "explore"})
    ordered += ranked
    for item in ordered:
        if len(chosen) >= limit:
            break
        title = normalized_title(item["title"])
        if any(c["arxiv_id"] == item["arxiv_id"] for c in chosen) or title in titles:
            continue
        if item["historical"] and historical >= 2:
            continue
        if any(teams[t] >= 3 for t in item["matched_teams"]):
            continue
        chosen.append(item)
        titles.add(title)
        historical += int(item["historical"])
        teams.update(item["matched_teams"])
    return chosen


def apply_ai(ranked, output):
    """AI only adjusts scores for existing IDs and provides bounded explanations."""
    if not isinstance(output, dict) or not isinstance(output.get("items"), list):
        raise ValueError("AI 输出必须包含 items 数组")  # noqa: TRY004 - one validation error contract for callers
    known = {c["arxiv_id"]: c for c in ranked}
    updated = {key: dict(value) for key, value in known.items()}
    seen = set()
    for result in output["items"]:
        if not isinstance(result, dict):
            raise ValueError("AI 条目格式错误")  # noqa: TRY004 - one validation error contract for callers
        aid = result.get("arxiv_id")
        score = result.get("relevance")
        reason = result.get("reason")
        evidence = result.get("evidence")
        if (
            aid not in known
            or aid in seen
            or isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
            or not 0 <= score <= 1
        ):
            raise ValueError("AI 包含未知/重复论文或无效分数")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 600:
            raise ValueError("AI 推荐理由无效")
        text = known[aid]["title"] + " " + known[aid]["abstract"]
        if (
            not isinstance(evidence, str)
            or not evidence.strip()
            or evidence.casefold() not in text.casefold()
        ):
            raise ValueError("AI 证据必须来自标题或摘要原文")
        seen.add(aid)
        extracted = result.get("features", [])
        if not isinstance(extracted, list) or len(extracted) > 3:
            raise ValueError("论文特征数量无效")
        labels = {d: [] for d in DIMENSIONS}
        for feature in extracted:
            if (
                not isinstance(feature, dict)
                or feature.get("dimension") not in DIMENSIONS
            ):
                raise ValueError("论文特征维度无效")
            label, quote = feature.get("label"), feature.get("evidence")
            if (
                not isinstance(label, str)
                or not label.strip()
                or len(label) > 160
                or label.casefold() not in text.casefold()
                or not isinstance(quote, str)
                or not quote.strip()
                or quote.casefold() not in text.casefold()
            ):
                raise ValueError("论文特征缺少原文依据")
            labels[feature["dimension"]].append(label)
        updated[aid].update(
            score=known[aid]["score"] * (0.5 + score),
            reason=reason,
            evidence=evidence,
            features=labels,
            feature_evidence=extracted,
            ai=True,
            relevance=score,
        )
    if seen != set(known):
        raise ValueError("AI 缺少候选论文")
    return sorted(
        (c for c in updated.values() if c["relevance"] >= 0.25),
        key=lambda c: (-c["score"], c["arxiv_id"]),
    )
