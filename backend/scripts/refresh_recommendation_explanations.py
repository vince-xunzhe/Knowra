"""Refresh an existing local AI selection's prose using metadata and Codex CLI."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from cloud_models import RecBatch, RecProfile
from config import load_config
from services.local_recommendations import LOCAL_USER, local_store
from services.personal_recommendation import replace_explanations, signature

from model_gateway.config import ensure_model_gateway_config
from model_gateway.recommendations import infer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-id", help="默认使用本地最近完成的批次")
    args = parser.parse_args()
    store = local_store()
    with store.session() as db:
        query = db.query(RecBatch).filter_by(user_id=LOCAL_USER, status="completed")
        if args.batch_id:
            query = query.filter_by(id=args.batch_id)
        batch = query.order_by(RecBatch.completed_at.desc()).first()
        if not batch:
            parser.error("没有已完成的本地精选")
        batch_id, original = batch.id, batch.items
        profile = db.get(RecProfile, LOCAL_USER).snapshot
    selected = [item for item in original if item.get("ai")]
    if not selected:
        parser.error("该批次没有 AI 精选；基础排序不通过此工具改写")
    cfg = ensure_model_gateway_config(load_config())
    for provider in cfg["model_gateway"]["providers"]:
        if provider["provider_type"] == "codex_cli":
            provider["enabled"] = True
    replacements = {}
    for start in range(0, len(selected), 3):
        chunk = selected[start : start + 3]
        output = infer(cfg, profile, chunk, provider_mode="cli")
        replacements.update(
            (item["arxiv_id"], item) for item in replace_explanations(chunk, output)
        )
        print(f"已验证 {len(replacements)}/{len(selected)} 篇说明", flush=True)
    with store.session() as db:
        batch = db.get(RecBatch, batch_id)
        if not batch or signature(batch.items) != signature(original):
            raise RuntimeError("原批次已发生变化，未覆盖任何说明，请重新运行")
        batch.items = [replacements.get(item["arxiv_id"], item) for item in original]
        db.commit()
    print(
        f"已更新 {len(replacements)} 篇说明；顺序、评分、批次时间与反馈未变", flush=True
    )


if __name__ == "__main__":
    main()
