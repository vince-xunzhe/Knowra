"""Local desktop worker lifecycle. No tokens are persisted or returned."""

import os
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(
    prefix="/api/recommendations/worker", tags=["local-recommendation-worker"]
)
_lock = threading.Lock()
_process = None


class StartWorker(BaseModel):
    url: str = Field(max_length=300)
    token: str = Field(min_length=20, max_length=200)


def _local_only():
    from config import is_cloud_mode

    if is_cloud_mode():
        raise HTTPException(404, "本机节点管理只在桌面端提供")


@router.get("")
def status():
    _local_only()
    with _lock:
        return {
            "running": _process is not None and _process.poll() is None,
            "exit_code": _process.poll() if _process is not None else None,
        }


@router.post("/start")
def start(body: StartWorker):
    global _process
    _local_only()
    parsed = urlparse(body.url)
    if (
        not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise HTTPException(400, "请提供云端服务根地址")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    ):
        raise HTTPException(400, "远端服务必须使用 HTTPS")
    if not body.token.startswith("krw_"):
        raise HTTPException(400, "无效节点凭证")
    with _lock:
        if _process is not None and _process.poll() is None:
            raise HTTPException(409, "本机节点已运行，请先停止再更换配置")
        root = Path(__file__).resolve().parents[2]
        env = dict(os.environ)
        env["KNOWRA_REC_WORKER_TOKEN"] = body.token
        _process = subprocess.Popen(
            [
                sys.executable,
                str(root / "backend/scripts/recommendation_worker.py"),
                "--url",
                body.url,
                "--provider",
                "cli",
            ],
            cwd=str(root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    return {"running": True}


@router.post("/stop")
def stop():
    _local_only()
    with _lock:
        if _process is not None and _process.poll() is None:
            # Terminate the worker AND its active CLI child to avoid orphan calls.
            import signal

            try:
                os.killpg(_process.pid, signal.SIGTERM)
                _process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(_process.pid, signal.SIGKILL)
                _process.wait(timeout=5)
            except ProcessLookupError:
                pass
    return {"running": False}


def shutdown_worker():
    stop()
