# Durable Library Tasks / 持久化后台任务

## Runtime / 运行方式

HTTP API -> `data/tasks.db` queue -> independent Python worker -> library/wiki.
The browser observes jobs; it does not own the extraction/curation/compilation/lint pipeline.
Interactive Ask/chat remain request-driven. Cloud sync remains in the authenticated client.

默认无需安装 Redis/Celery。API 与 worker 使用同一 Python 虚拟环境，沿用当前模型网关、
扫描目录及凭证环境。论文、Wiki、筛选、健检不再占用 HTTP 进程执行。

| Configuration | Behavior |
| --- | --- |
| `KNOWRA_WORKER_MODE=auto` (default) | Local API automatically starts a detached worker. |
| `KNOWRA_WORKER_MODE=managed` | Explicit local process supervision. |
| `KNOWRA_WORKER_MODE=external` | API only queues; supervisor/operator starts the worker below. |
| `KNOWRA_TASK_DB=/absolute/path/tasks.db` | Optional queue location; API and worker must share it. |

External single-library deployments run:

```bash
cd backend
KNOWRA_WORKER_MODE=external .venv/bin/python scripts/task_worker.py
```

Use the same code, data volume, configuration and environment as the API. Initialize a new
library by starting its API first. Do not put the SQLite queue or library on an unreliable
network filesystem. Multi-tenant cloud execution is deliberately rejected until a
tenant-scoped shared queue is configured; this is not a distributed multi-host queue.

SQLite 下始终只有一个重型任务执行者，不按 CPU 核数盲目扩大写入并发。支持本机
Codex CLI 与 API 模型混用；不为 Codex 强制要求 OpenAI API Key。API provider 的
凭证仍由网关按任务校验。POSIX worker 降低调度优先级，优先保障前端浏览。

## Recovery / 恢复语义

- API restart or closing a browser does not terminate an already-running detached worker.
- A lifetime file lock prevents duplicate workers, including across API restarts.
- Managed mode checks worker availability every five seconds, even with all browser tabs closed.
- Worker heartbeat and job progress use a separate SQLite WAL database with short transactions.
- Duplicate active requests with the same payload share a job; conflicting same-module requests return 409.
- `/api/pipeline/run` supports `Idempotency-Key`, including after the job finishes.
- Only the lock-owning replacement worker marks abandoned running jobs `interrupted`.
- Use the cross-page **后台任务** panel to resume a failed/interrupted job. Successful paper/page
  checkpoints and completed pipeline stages are retained. Resume never steals a live worker's task.

模型请求无法与本地数据库构成原子事务。若恰好在模型返回与检查点保存之间崩溃，
恢复时可能再次调用该模型。因此采用“明确中断、人工恢复”的至少一次语义，
**不承诺模型调用 exactly-once 或零重复计费**。队列不持久化 API Key。
修改模型配置后，后续执行项按现有网关加载配置，不会把整批凭证写入任务记录。

The backend pipeline performs scan -> extract -> curate -> accept -> incremental wiki compile -> lint.
Cloud sync follows in the logged-in initiating client; after closing/reopening that client, use
the existing Sync action. No browser credentials are copied to the worker.

## Operations / 运维

### Runtime upgrades / 运行时升级

Managed workers publish a runtime fingerprint (Python sources, interpreter, PATH,
CODEX_CLI_PATH and PYTHONPATH). The API publishes the desired fingerprint every five
seconds. On a mismatch, the worker finishes its **current queue job**, releases its
lifetime lock and exits before claiming another job. The API then starts a replacement
with its current environment. A batch/pipeline is one job; upgrading does not interrupt
a model call midway or replay successful work. Source files should be deployed together,
not edited repeatedly during an active production job.

托管 worker 不再因为心跳正常就永久沿用旧代码。升级时先完成当前整批任务，再换代，
不强杀正在运行的模型请求。修改启动环境变量后需要重启 API，worker 才能继承新环境；
网页中的模型/Provider 配置仍在后续执行时重新加载。指纹不包含密钥或认证文件。
外部托管模式不自动退出或替换进程，升级由运维在任务完成后进行。

`/api/jobs/runtime` reports `restart_pending` while a managed worker is draining.
`legacy_worker: true` identifies workers started before this protocol existed: once,
after verifying no queued/running jobs, stop that old worker and restart the API. Do not
kill a busy legacy worker merely to upgrade. The heartbeat schema stays backward-compatible.

CLI resolution is repeated for every call, including desktop app updates. Precedence:
explicit Provider executable path, `CODEX_CLI_PATH`, PATH, then known installation locations
(including old flat and new embedded Codex/ChatGPT app bundles). Invalid explicit paths
fail clearly rather than silently choosing another installation. No executable is installed
automatically. Paper jobs check CLI availability **before** clearing old extraction results;
missing/non-executable CLI errors are configuration failures, not retryable parsing failures.
Availability checks do not call a model or verify login/network/model access.

首次升级需手动替换不支持协议的旧 worker；之后由托管模式自动完成安全换代。
缺少 CLI 时修复配置后再重试，既有论文结果不会因批处理启动预检失败而被删除。

- `GET /api/jobs/runtime`: selected policy, worker PID and heartbeat.
- `GET /api/jobs`: recent tasks; `GET /api/jobs/{id}`: durable progress/result.
- `POST /api/jobs/{id}/resume`: explicit checkpoint resume.
- Logs: `data/logs/task-worker.log`. Queue: `data/tasks.db` (+ SQLite WAL/SHM).
- API shutdown intentionally leaves the worker alive. To stop all work, terminate the PID from
  `/api/jobs/runtime`; on the next worker start unfinished jobs become interrupted. An active API
  in managed mode can restart a stopped worker when polled; use external mode for manual supervision.
- Back up queue/library via SQLite backup APIs, not a bare copy of live `.db` files.

## Verification

```bash
backend/.venv/bin/python -m pytest -q backend/tests/test_task_queue.py backend/tests/test_task_executor.py backend/tests/test_paper_background_dispatch.py
cd frontend
node --test tests/polling.test.mjs
npm run build
```

Fault tests use temporary queues, fake handlers and independent OS processes, not paid model
requests or the user's paper library. End-to-end model quality/latency still depends on the
selected provider and should be validated on representative real batches before release.
