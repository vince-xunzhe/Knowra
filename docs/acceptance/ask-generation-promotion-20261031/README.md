# Ask generation 审核、切换与回滚验收（2026-10-31）

## 目标与结果

本轮只增加生产资格控制面，不构建、复制、修改或启用真实 generation。不可变 shadow
artifact 继续保持 `production_eligible=false`；审核通过后，独立 control root 保存不可变
promotion record，并以 `active.json` 原子指针赋予某次运行 production eligibility。

- promotion 绑定 generation manifest/index SHA-256、source/dense digest、物理 index identity、
  审核人、带时区审核时间和验收报告 SHA-256。
- promotion 全量只读检查 evidence locator：页码、页内字符范围、manifest ID 与
  source-file SHA-256 缺一不可。旧 artifact 仍可 `observe`，不能进入 `assist`。
- promote/rollback 在同一个进程锁下校验 expected active SHA-256，写不可变切换 intent，
  再以同目录临时文件、`fsync` 和 `os.replace` 原子更新 active pointer。
- `assist` 只解析 `KNOWRA_ASK_GENERATION_CONTROL`；直接设置 shadow root 不会调用路由
  或回答模型。`observe` 的旧接口和输出保持兼容。
- 导入模块、安装依赖和默认 `off` 都不会创建 control、切换 pointer 或读取 generation。

## 合成验收范围

- 正常 promotion：generation 所有文件 size/mtime 前后不变，active/promotion 绑定一致，
  trace 显示 production eligible，但不包含证据正文。
- locator 门槛：即使重新签署 index/generation hash，缺失 source hash 仍拒绝 promotion；
  同一 artifact 仍可只读 observe。
- 原子故障：在 `os.replace` 注入失败，旧 active 字节完全不变，解析仍指向旧 generation。
- 持久化边界：替换后目录 `fsync` 失败会明确返回 uncertain；`status` 能解析已经成为
  source of truth 的新 pointer，避免错误重试或误报旧版本仍 active。
- 回滚：错误 expected digest 拒绝；正确 digest 原子指回既有审核记录，目标在切换前重新
  校验 manifest/index hash 与物理 identity。
- 篡改与路径：审核记录篡改、index identity 变化、symlink control、live-data 重叠均
  fail closed。

## 尚未执行

- 未对真实 shadow 执行 promote，未创建真实 control root，未修改原论文、Wiki、数据库、
  配置或备份。
- 未启动 API/Web/worker，未设置真实运行环境变量，未部署或执行真实用户 Ask。
- 未重新发送修订后的第 3 问证据给外部模型；该项仍需单独授权。
- 本轮没有 UI 变化，因此没有截图验收。

## 验证结果

- 专项：11 项 generation control、20 项 Ask 接线测试通过。
- 统一检查：29 项启动、212 项后端、前端 unit／类型／构建、零 warning lint 和
  8 项隔离浏览器回归全部通过。
- 环境：Python 3.12.13，Node 24.14.1；仅使用本 worktree 的独立环境与合成数据。

## 复现

```bash
PYTHONPATH=backend backend/.venv/bin/python -m unittest -v \
  backend.tests.test_ask_generation_control \
  backend.tests.test_ask_shadow_integration

NODE_BIN=/Users/vince/.local/bin/node ./scripts/check.sh
```
