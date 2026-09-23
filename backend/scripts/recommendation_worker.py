"""Run with PYTHONPATH=backend:. python backend/scripts/recommendation_worker.py --help."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from config import load_config
from services.arxiv_service import search_arxiv
from services.personal_recommendation import (
    ai_shortlist,
    apply_ai,
    aware,
    base_id,
    build_profile,
    query_plan,
    rank_candidates,
    select_diverse,
    utcnow,
)

from model_gateway.recommendations import infer


class WorkerClient:
    def __init__(self, url, token):
        if not url.startswith(("https://", "http://127.0.0.1:", "http://localhost:")):
            raise ValueError("远端 worker 连接必须使用 HTTPS")
        if not token.startswith("krw_"):
            raise ValueError("请配置 KNOWRA_REC_WORKER_TOKEN（站内生成的节点凭证）")
        self.url = url.rstrip("/") + "/api/cloud/personal-recommendations/worker"
        self.token = token

    def post(self, path, body=None):
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(body or {}).encode(),
            method="POST",
            headers={
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
            },
        )

        # Do not forward credentials through redirects to another origin.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        with urllib.request.build_opener(NoRedirect()).open(
            request, timeout=30
        ) as response:
            return json.load(response)


def fetch_candidates(job, *, search=search_arxiv, pause=time.sleep):
    """Bounded metadata collection with pagination and overlapping date windows."""
    now = aware(job["snapshot"]["as_of"])
    cached = {
        base_id(c.get("arxiv_id")): c
        for c in job.get("candidates", [])
        if base_id(c.get("arxiv_id"))
    }
    queries = query_plan(job["snapshot"])
    failures = 0
    for query in queries:
        since = now - timedelta(days=30)
        dated = query + f" AND submittedDate:[{since:%Y%m%d}0000 TO {now:%Y%m%d}2359]"
        for start in (0, 40):
            try:
                rows = search(dated, max_results=40, start=start)
                for row in rows:
                    row = dict(row)
                    row["published"] = (
                        aware(row.get("published")).isoformat()
                        if aware(row.get("published"))
                        else None
                    )
                    cached[base_id(row["arxiv_id"]) or row["arxiv_id"]] = row
                pause(3.1)
                if len(rows) < 40:
                    break
            except Exception:  # noqa: BLE001 - external transport/model failures use a bounded fallback
                failures += 1
                pause(3.1)
                break
        try:
            rows = search(query, max_results=20, start=0, sort_by="relevance")
            for row in rows:
                row = dict(row)
                row["published"] = (
                    aware(row.get("published")).isoformat()
                    if aware(row.get("published"))
                    else None
                )
                cached[base_id(row["arxiv_id"]) or row["arxiv_id"]] = row
        except Exception:  # noqa: BLE001 - external transport/model failures use a bounded fallback
            failures += 1
        pause(3.1)
    if failures and not cached:
        raise RuntimeError("retrieval_failed")
    # Send only metadata, never URLs supplied by publishers or full paper text.
    ranked = rank_candidates(
        job["snapshot"],
        list(cached.values()),
        excluded=job.get("excluded", []),
        now=now,
    )
    keys = ("arxiv_id", "title", "abstract", "authors", "primary_category", "published")
    selected = [{k: c.get(k) for k in keys} for c in ranked[:600]]
    return selected, failures


def process(
    job,
    cfg,
    *,
    provider="cli",
    no_ai=False,
    candidates=None,
    reserve=lambda amount, provider: None,
    input_rate=0,
    output_rate=0,
):
    failures = 0
    if candidates is None:
        candidates, failures = fetch_candidates(job)
    ranked = rank_candidates(
        job["snapshot"],
        candidates,
        excluded=job.get("excluded", []),
        now=aware(job["snapshot"]["as_of"]),
    )[:30]
    output = None
    note = "部分检索失败，使用已获取元数据" if failures else None
    if ranked and not no_ai:
        if provider == "api" and (input_rate <= 0 or output_rate <= 0):
            note = "基础排序：未配置可核算的 API 价格"
        else:
            try:
                output = infer(
                    cfg,
                    job["snapshot"],
                    ai_shortlist(ranked),
                    provider_mode=provider,
                    reserve=reserve,
                    input_rate=input_rate,
                    output_rate=output_rate,
                )
                apply_ai(
                    ai_shortlist(ranked), output
                )  # Validate before publishing; no extra retries/cost.
            except urllib.error.HTTPError as exc:
                if exc.code != 409:
                    raise
                output, note = None, "基础排序：本月预算不足"
            except Exception:  # noqa: BLE001 - external transport/model failures use a bounded fallback
                output, note = None, "基础排序：模型暂不可用"
    elif no_ai:
        note = "基础排序：已关闭 AI"
    return {
        "lease": job.get("lease", "local"),
        "candidates": candidates,
        "ai_output": output,
        "note": note,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Knowra 论文推荐节点（默认 Codex CLI）"
    )
    parser.add_argument(
        "--local", action="store_true", help="直接使用本机知识库和持久化队列，无需云端"
    )
    parser.add_argument("--url", default=os.environ.get("KNOWRA_CLOUD_URL", ""))
    parser.add_argument("--provider", choices=["cli", "api"], default="cli")
    parser.add_argument("--once", action="store_true", help="执行一个任务后退出")
    parser.add_argument(
        "--no-ai", action="store_true", help="仅验证基础排序，不调用模型"
    )
    parser.add_argument(
        "--local-input",
        type=Path,
        help="本地 JSON，包含 papers、candidates 和可选 current_focus",
    )
    parser.add_argument("--output", type=Path, help="本地测试结果路径（无需云端凭证）")
    parser.add_argument("--input-cny-per-million", type=float, default=0)
    parser.add_argument("--output-cny-per-million", type=float, default=0)
    args = parser.parse_args()
    cfg = {} if args.no_ai else load_config()
    if not args.no_ai and args.provider == "cli":
        # --provider cli is an explicit runtime choice. Enable the CLI adapter
        # in memory only; never rewrite the user's settings/credential files.
        from model_gateway.config import ensure_model_gateway_config

        cfg = ensure_model_gateway_config(cfg)
        for entry in cfg["model_gateway"]["providers"]:
            if entry["provider_type"] == "codex_cli":
                entry["enabled"] = True
    if args.local_input:
        if not args.output:
            parser.error("--local-input 需要 --output")
        if args.provider == "api" and not args.no_ai:
            parser.error(
                "付费 API 测试需通过持久化 worker 队列预留预算；样本测试请使用默认 CLI"
            )
        data = json.loads(args.local_input.read_text())
        now = utcnow()
        snapshot = build_profile(
            data["papers"],
            data.get("nodes", []),
            data.get("current_focus", ""),
            now=now,
        )
        snapshot["as_of"] = now.isoformat()
        job = {"snapshot": snapshot, "candidates": data["candidates"]}
        result = process(
            job,
            cfg,
            provider=args.provider,
            no_ai=args.no_ai,
            candidates=data["candidates"],
        )
        ranked = rank_candidates(snapshot, data["candidates"], now=now)[:30]
        if result["ai_output"]:
            ranked = apply_ai(ai_shortlist(ranked), result["ai_output"])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(
                {"items": select_diverse(ranked), "note": result["note"]},
                ensure_ascii=False,
                indent=2,
            )
        )
        print(f"本地推荐结果已保存：{args.output}")
        if ranked and not args.no_ai and result["ai_output"] is None:
            print("AI smoke test 未通过：" + str(result["note"]))
            raise SystemExit(1)
        return
    if args.local:
        from services.local_recommendations import LocalWorkerClient

        client = LocalWorkerClient()
    else:
        client = WorkerClient(args.url, os.environ.get("KNOWRA_REC_WORKER_TOKEN", ""))
    stop = threading.Event()
    state = {"health": "ready"}

    def heartbeat():
        while not stop.is_set():
            try:
                client.post("/heartbeat", state)
            except Exception:  # noqa: BLE001 - external transport/model failures use a bounded fallback
                print("节点心跳发送失败；界面将显示离线状态", flush=True)
            stop.wait(60)

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        while True:
            job = None
            try:
                job = client.post("/claim")["job"]
                if job:
                    state["health"] = "busy"

                    def reserve(amount, provider, job=job):
                        return client.post(
                            f"/{job['id']}/reserve",
                            {
                                "lease": job["lease"],
                                "amount_cny": amount,
                                "provider": provider,
                            },
                        )

                    result = process(
                        job,
                        cfg,
                        provider=args.provider,
                        no_ai=args.no_ai,
                        reserve=reserve,
                        input_rate=args.input_cny_per_million,
                        output_rate=args.output_cny_per_million,
                    )
                    client.post(f"/{job['id']}/complete", result)
                    state["health"] = (
                        "model_unavailable"
                        if result["note"] == "基础排序：模型暂不可用"
                        else "ready"
                    )
                    print(
                        "推荐批次已完成"
                        + (f"（{result['note']}）" if result["note"] else ""),
                        flush=True,
                    )
            except Exception:  # noqa: BLE001 - external transport/model failures use a bounded fallback
                state["health"] = "failed"
                print("推荐任务暂未完成；请检查节点连接和模型状态", flush=True)
                if job:
                    try:
                        client.post(
                            f"/{job['id']}/fail",
                            {"lease": job["lease"], "reason": "worker_error"},
                        )
                    except Exception:  # noqa: BLE001 - preserve lease-based recovery on transport failure
                        print("失败状态未送达，队列将在租约到期后恢复任务", flush=True)
                if args.once:
                    raise SystemExit(1)
            if args.once:
                break
            stop.wait(30)
    finally:
        stop.set()
        thread.join(timeout=1)


if __name__ == "__main__":
    main()
