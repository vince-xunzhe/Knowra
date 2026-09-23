"""Single background lint job with a durable result for page reloads."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from services.wiki_compiler import WIKI_DIR


class WikiLintJob:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.state = {"job_id": None, "status": "idle", "phase": "", "error": None,
                      "started_at": None, "finished_at": None, "use_llm": True}
        self.result = None
        self.result_job_id = None
        if path.is_file():
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                self.state.update(saved["state"])
                self.result = saved.get("result")
                self.result_job_id = saved.get("result_job_id")
                if self.state["status"] == "running":
                    self.state.update(status="failed", phase="检查中断",
                                      error="后端已重启，请重新运行健康检查。")
            except (OSError, ValueError, KeyError, TypeError):
                pass

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def report(self):
        with self.lock:
            return {"job_id": self.result_job_id, "result": self.result}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".wiki-lint-", delete=False) as f:
                name = f.name
                json.dump({"state": self.state, "result": self.result,
                           "result_job_id": self.result_job_id}, f, ensure_ascii=False)
            os.replace(name, self.path)
        finally:
            if name and os.path.exists(name):
                os.unlink(name)

    def start(self, use_llm=True):
        with self.lock:
            if self.state["status"] == "running":
                return self.snapshot()
            previous = self.state
            self.state = {"job_id": str(uuid4()), "status": "running", "phase": "准备检查",
                          "error": None, "started_at": datetime.now(timezone.utc).isoformat(),
                          "finished_at": None, "use_llm": use_llm}
            try:
                self._save()
                threading.Thread(target=self._run, args=(use_llm,), daemon=True).start()
            except Exception:
                self.state = previous
                self._save()
                raise
            return self.snapshot()

    def _progress(self, phase):
        with self.lock:
            self.state["phase"] = phase

    def _run(self, use_llm):
        from database import SessionLocal
        from services.wiki_lint_service import run_lint

        try:
            with SessionLocal() as db:
                result = run_lint(db, use_llm=use_llm, on_progress=self._progress)
            warning = result.get("judgment", {}).get("error")
            with self.lock:
                self.result = result
                self.result_job_id = self.state["job_id"]
                self.state.update(status="warning" if warning else "completed",
                                  phase="规则检查完成，Agent 判定未完成" if warning else "检查完成",
                                  error=warning, finished_at=datetime.now(timezone.utc).isoformat())
                self._save()
        except Exception as exc:
            with self.lock:
                self.state.update(status="failed", phase="检查失败", error=str(exc),
                                  finished_at=datetime.now(timezone.utc).isoformat())
                try:
                    self._save()
                except OSError:
                    pass


lint_job = WikiLintJob(WIKI_DIR.parent / "wiki-lint-job.json")
