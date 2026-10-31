# PDF 解析失败恢复验收（2026-10-31）

## 根因与修复

5 个失败页不是语料缺失，而是 `pypdf 5.1.0` 解析嵌入 Type1 字体时的确定性缺陷：
字体编码含未知、且不是 `uniXXXX` 形式的 glyph 名称时，`_type1_alternative()` 会读取尚未
赋值的局部变量并抛出 `UnboundLocalError`。

证据构建现在采用有界、逐页、懒加载的双解析器策略：

- pypdf 仍是主解析器；正常页不会打开备用解析器，既有文本及成本路径保持不变。
- 只有文档打开或单页访问／抽取失败时，才从同一份已哈希的内存快照调用锁定版本的
  `pypdfium2 4.30.0`；不重新读取路径，也不吞掉后续页面。
- 每页记录实际 parser/version、主解析异常类型和备用异常类型。主备均失败仍保存 error
  状态，不写异常消息或伪造正文；文档级主失败／备用失败也分别记录类型。
- 旧 evidence manifest 没有新增 provenance 字段时仍可读取；新字段存在时会进行语义校验，
  防止把主解析结果伪装成备用恢复结果。

## 合成回归

测试现场构造一页最小 Type1 PDF，字体包含未知 glyph。它会在真实 `pypdf 5.1.0` 路径
触发相同 `UnboundLocalError`，而 PDFium 恢复出预期文字；这不是 mock 异常。

另覆盖：主解析成功时备用解析器绝不打开、文档打开失败后可整篇回退、主备均失败保留
两种错误类型、失败页不阻断后续页、manifest provenance 篡改拒绝。

## 真实隔离 generation

从已验证恢复副本构建新的、独立且未启用的 generation：

- 198/198 篇、5,249 页，失败页 5 → 0，论文级失败 0。
- 旧有 5,244 个成功页面的 text hash／字符数全部不变；其 11,170 个页内文本 spans 全部
  保留。部分 chunk ID 因恢复页带回正确章节继承而变化，但对应文本 span 未变化。
- 恢复 18,246 个字符、12 个新 evidence spans；总 evidence chunks 11,170 → 11,182，
  claims 保持 24,530。
- 198 个源文件 SHA-256、198 份 evidence manifests、198 份 compile plans、全部索引正文和
  locator 交叉验证通过；SQLite `quick_check=ok`。
- 临时 control 以 `accepted_failed_pages=0` 完成两次 promotion 和一次 rollback；同批 5 问
  均在每题最多 4 条证据内达到 coverage，所有 source hash 和页内 locator 完整。
- 恢复副本与上一份 generation 的全量 SHA-256／size／mtime manifest 在构建、检索和切换
  演练前后完全相同。没有生产 pointer 或日常数据写入。

## 模型与真实界面验收

- 用户授权的既定第 3 问及 4 段证据已通过官方 Codex CLI 发送给 GPT-6 Astra；调用
  `returncode=0`，回答引用且只引用允许集合内的 4 个 evidence ID。逐句核验确认其四项
  结论分别受到原文支持，并明确保留“材料不足以建立完整时间顺序”的边界。
- 隔离 API 8127／React 5207 使用空会话库、external worker、已审核零失败页 generation 和
  同一官方 CLI 完成真实浏览器交互。页面显示 `codex-cli/gpt-6-astra`、6 步、57.1 秒、4 条
  可展开页码／字符 locator；刷新后会话标题、回答、trace 和引用均保留。服务正常 shutdown，
  两端口均释放；日常知识库和 production pointer 未接触。
- 这次真实 UI 回答的所有结论虽受证据支持，但 4 条上下文中 3 条来自同一篇 DDIM 论文，
  只覆盖 DDPM→DDIM 及 DDIM 在 LDM 中的应用，遗漏同一 generation 已检出的可学习方差和
  reverse SDE／probability-flow ODE。链路与拒绝越界行为通过，跨阶段覆盖尚未达到最终目标，
  因而仍不发布 PR。
- 签名核验说明：
  受限沙箱内的 `codesign`／`spctl` 曾分别误报签名修改和签名子系统内部错误；在允许访问
  macOS 系统信任服务后，同一只读校验确认应用 `valid on disk`、满足 Designated
  Requirement，Gatekeeper 接受且来源为 `Notarized Developer ID`。因此签名不是阻塞；
  内置 CLI 的信任来自已签名 bundle 对资源的完整封印，不以该资源具有独立签名为前提。
- 未启用 production、未部署、未 push 或创建 PR。

## 验证

- PDF evidence 专项 15 项通过；证据→编译→检索→引用→generation control 相关 98 项通过。
- 统一检查通过：29 项启动、216 项后端、前端 unit／类型／构建、零 warning lint 和
  8 项隔离浏览器回归。环境为 Python 3.12.13、Node 24.14.1，本 worktree 独立环境。
