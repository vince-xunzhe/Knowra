# 个性化推荐验证记录

日期：2026-09-23；Python 3.9 本地开发环境。未读取真实用户知识库或修改生产数据。

## 工程验证

| 检查 | 结果 |
| --- | --- |
| 实施前推荐 / 网关 / 云同步定向基线 | 54 passed |
| 完成后 `backend/.venv/bin/python -m pytest -q` | 262 passed，8 skipped；跳过项需要 PostgreSQL 测试环境 |
| 新增推荐测试 | 33 passed；涵盖用户隔离、真入库、重复曝光归因、月份切换预算、分页/缓存、租约、模型契约与本机进程控制 |
| 桌面 `npm run build` | 通过；已有大 bundle 提示 |
| 桌面改动文件 ESLint | 通过 |
| 手机 `npm run tsc` | 通过 |
| 新增 Python 文件 Ruff（目标 py39） | 通过 |
| 真实 Codex CLI smoke | 退出码 0，2 篇 AI 精选，6 个有原文依据的特征，无降级 |
| 合成时间回放 | 4 个场景通过；仅验证代理评估协议 |
| `git diff --check` | 通过 |

CLI smoke 输入为 `backend/tests/fixtures/recommendation_smoke.json`，不是用户数据。API 路径使用 mock 验证预算先行、输出上限和无工具调用，未发起真实付费 API 请求。入库测试用临时文件与内存数据库验证持久化及幂等，未下载真实候选 PDF。

全仓 Ruff / mypy **未通过**：首次扫描分别报告 887 个 lint 问题、574 个类型错误，包含仓库既有规则问题、缺失依赖类型和 SQLAlchemy 动态 Base/Column 类型不兼容；新增云模型也沿用同样的动态 Base 模式。本次没有批量修复无关模块，不能把新增文件定向检查通过当作全仓检查通过。工具安装在临时目录，没有改项目依赖。AGENTS 指定的 `evals/run_eval.sh` 与 `evals/summarize_metrics.py` 在仓库中不存在，不能运行其标准评估命令。

PostgreSQL 迁移、RLS 和并发锁尚未在真实数据库验证；应用层租户隔离与预算/租约行为已在 SQLite 测试。生产发布前需在测试 Postgres 验证迁移与角色权限。手机仅完成编译验证，尚无真机交互验收。

## 基线与新排序：只报告合成代理指标

在同一 4 场景合成候选集（splatting、robotics、diffusion、multimodal）中，每场景包含 1 个已知未来正例与多个较新的无关候选：

| 平均指标 | 时间排序基线 | 内容排序 |
| --- | --- | --- |
| 已知未来正例 Recall@10 | 0 | 1 |
| 首个已知未来正例倒数排名 | 0 | 1 |
| 每场景输出数 | 10 | 1 |

这些样本特意检查“排除无关论文、不凑数”的契约，**不是质量 benchmark，不是效果提升证据**。没有把未采纳论文标成负例；没有修改现有验证划分。当前真实在线采纳率、真实知识库回放和 AI 相对内容排序收益均未测得。30% 是用户确认的 14 天浏览后采纳目标，需收集成熟在线样本。

命令、启动和回滚方式见 [运行手册](RECOMMENDATION-RUNBOOK.md)，实现边界见 [设计文档第 11 节](RECOMMENDATION-PERSONALIZATION-DESIGN.md#11-首版落地说明以此为准)。
