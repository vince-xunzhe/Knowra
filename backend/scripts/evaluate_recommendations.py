"""Time-cutoff replay using future positive adoptions; not online conversion."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from services.personal_recommendation import (
    aware,
    base_id,
    build_profile,
    rank_candidates,
    select_diverse,
)


def evaluate(data):
    rows = []
    for case in data["cases"]:
        cutoff = aware(case["cutoff"])
        papers = [
            p
            for p in case["papers"]
            if aware(p.get("created_at")) and aware(p["created_at"]) <= cutoff
        ]
        profile = build_profile(papers, now=cutoff)
        eligible = {}
        for item in case["candidates"]:
            aid = base_id(item.get("arxiv_id"))
            if (
                aid
                and aid not in profile["library_ids"]
                and aware(item.get("published"))
                and aware(item["published"]) <= cutoff
            ):
                eligible[aid] = {**item, "arxiv_id": aid}
        positives = {
            base_id(p["arxiv_id"])
            for p in case["future_adoptions"]
            if cutoff < aware(p["created_at"]) <= cutoff + timedelta(days=14)
        } - set(profile["library_ids"])
        positives.discard(None)
        baseline = sorted(
            eligible.values(), key=lambda p: aware(p["published"]), reverse=True
        )[:10]
        personalized = select_diverse(
            rank_candidates(profile, list(eligible.values()), now=cutoff)
        )

        def scores(items, positives=positives):
            ranks = [
                i + 1 for i, item in enumerate(items) if item["arxiv_id"] in positives
            ]
            return {
                "positive_recall_at_10": len(ranks) / len(positives)
                if positives
                else None,
                "reciprocal_rank": 1 / min(ranks) if ranks else 0,
                "selected": len(items),
            }

        rows.append(
            {
                "id": case["id"],
                "known_future_positives": len(positives),
                "candidate_positive_coverage": len(positives & eligible.keys())
                / len(positives)
                if positives
                else None,
                "baseline_time_sort": scores(baseline),
                "personalized_content": scores(personalized),
            }
        )
    return {
        "protocol": "positive-time-replay-v1",
        "dataset": data.get("dataset", "unspecified"),
        "proxy_only": True,
        "online_adoption_rate": None,
        "caveat": "未采纳保持未标注；回放召回率不能替代真实浏览后14天采纳率，也不能证明模型质量提升。",
        "cases": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(json.loads(args.input.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(
        f"Proxy replay written to {args.output}; online adoption rate remains unavailable."
    )


if __name__ == "__main__":
    main()
