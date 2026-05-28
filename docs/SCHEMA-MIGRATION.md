# Schema 迁移：SQLite → Postgres

本文档列出每一张现有 SQLite 表的 Postgres 等价 schema、`user_id` 注入点、
RLS policy、以及现有数据如何映射到多租户模型。

> **遵循契约**：本地 SQLite 和云 Postgres 的 schema 必须**1:1 对齐**，
> 除了 `user_id` 列：本地可空（隐式 = 当前用户），云端必填 + RLS。
> 任何 schema 变动必须同步两边的 migration。

---

## 0. 总览

| 表 | 行数（当前）| 后台同步策略 |
|---|---|---|
| `papers` | 32 | 全量字段同步，**除 `extracted_text` 和 `chat_history` 不上云**（隐私 + 体积） |
| `knowledge_nodes` | 523 | 全量同步，包含 `embedding` JSON 列 |
| `knowledge_edges` | 3635 | 全量同步 |
| `llm_calls` | ~∞ | **不同步**。仅本地遥测，云上 v1 不分析 |
| `users` | 新增 | 仅云端有；映射 Supabase `auth.users` |
| `sync_state` | 新增 | 桌面 ↔ 云的同步水位线 |
| `wiki_files` | 新增 | 云端 .md 文件的元数据索引（实际内容在 Supabase Storage） |

---

## 1. 通用约定

### 1.1 `user_id` 注入

- 类型：`UUID`（与 Supabase `auth.users.id` 一致）
- 位置：每张多租户表的第二列（第一列仍是 `id`）
- 本地 SQLite：`user_id TEXT NULL`（隐式 = 当前桌面用户）
- 云 Postgres：`user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE`
- 索引：每张表必加 `(user_id)` 和 `(user_id, <常用过滤列>)`

### 1.2 RLS Policy 模板

每张多租户表统一以下 policy：

```sql
ALTER TABLE <table> ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON <table>
  FOR ALL
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());
```

> `USING` 控制读，`WITH CHECK` 控制写。两个都要，否则用户可以伪造 user_id 写别人的数据。

### 1.3 同步水位线（updated_at）

所有多租户表统一加：

```sql
updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
```

桌面端 SQLite 用 trigger 维护：

```sql
CREATE TRIGGER <table>_updated_at AFTER UPDATE ON <table>
  BEGIN UPDATE <table> SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = NEW.id; END;
```

Sync push 时按 `WHERE updated_at > last_synced_at` 选 delta。

### 1.4 ID 策略

| 选项 | 选定 | 理由 |
|---|---|---|
| 沿用 SQLite 的 INT AUTOINCREMENT | ❌ | 多设备插入会冲突 |
| 改为云端 UUID | ✅（最终目标） | 全局唯一，多设备无冲突；现有数据迁移时一并赋 UUID |
| 双键：本地 INT + 云端 UUID | ❌ | 引入两套主键复杂度 |

**实施策略**：Phase 1 启动时一次性把所有现有 ID 转 UUID（在桌面端 migration 内完成）。
对外暴露的 API 主键全部 UUID；旧 INT id 作为 `legacy_id` 列保留 6 个月以便回查。

---

## 2. `papers` 表

### 2.1 当前 SQLite

```python
class Paper(Base):
    __tablename__ = "papers"

    id = Column(Integer, primary_key=True, index=True)
    filepath = Column(String, unique=True, nullable=False)
    filename = Column(String, nullable=False)
    file_hash = Column(String, nullable=False)
    num_pages = Column(Integer, nullable=True)
    extracted_text = Column(Text, nullable=True)       # ❌ 不上云
    first_page_image_path = Column(String, nullable=True)
    title = Column(String, nullable=True)
    authors = Column(JSON, default=list)
    processed = Column(Boolean, default=False)
    processed_at = Column(DateTime, nullable=True)
    extraction_model = Column(String, nullable=True)
    paper_category_model = Column(String, nullable=True)
    paper_category_override = Column(String, nullable=True)
    raw_llm_response = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    processing_status = Column(String, default="scanning")
    retry_count = Column(Integer, default=0)
    last_error_stage = Column(String, nullable=True)
    last_error_reason = Column(Text, nullable=True)
    last_error_recoverable = Column(Boolean, nullable=True)
    openai_file_id = Column(String, nullable=True)
    openai_vector_store_id = Column(String, nullable=True)
    openai_thread_id = Column(String, nullable=True)
    thread_created_at = Column(DateTime, nullable=True)
    chat_history = Column(JSON, default=list)          # ❌ 不上云
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
```

### 2.2 Postgres 等价

```sql
CREATE TABLE papers (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

    -- 文件标识
    filepath        TEXT NOT NULL,           -- 桌面端的本地路径；云端只读
    filename        TEXT NOT NULL,
    file_hash       TEXT NOT NULL,
    num_pages       INTEGER,

    -- 内容元数据（不含全文）
    title           TEXT,
    authors         JSONB DEFAULT '[]'::jsonb,
    paper_category_model    TEXT,
    paper_category_override TEXT,

    -- 处理状态
    processed              BOOLEAN DEFAULT FALSE,
    processed_at           TIMESTAMPTZ,
    extraction_model       TEXT,
    processing_status      TEXT DEFAULT 'scanning',
    retry_count            INTEGER DEFAULT 0,
    last_error_stage       TEXT,
    last_error_reason      TEXT,
    last_error_recoverable BOOLEAN,
    error                  TEXT,

    -- 抽取结果（结构化）
    raw_llm_response       TEXT,             -- 完整 LLM 响应 JSON
    notes                  TEXT,             -- 用户笔记 markdown

    -- 时间戳
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- 历史 ID（迁移期保留）
    legacy_id              INTEGER,

    UNIQUE (user_id, file_hash)              -- 同一 user 不允许重复入库
);

CREATE INDEX papers_user_id_idx ON papers (user_id);
CREATE INDEX papers_user_status_idx ON papers (user_id, processing_status);
CREATE INDEX papers_user_processed_idx ON papers (user_id, processed, processed_at);

ALTER TABLE papers ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON papers
  FOR ALL USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
```

### 2.3 故意不上云的列

| 列 | 原因 |
|---|---|
| `extracted_text` | 整篇 PDF 文本（可能很大）；侵犯版权风险 |
| `chat_history` | 单篇追问历史；可能含 PDF 原文摘录 |
| `first_page_image_path` | 桌面端本地缓存路径，无意义 |
| `openai_file_id` / `openai_vector_store_id` / `openai_thread_id` / `thread_created_at` | OpenAI 端资源 ID，依赖桌面的 OpenAI 账号 |

> **关键设计**：移动端展示一篇论文时拿到的是结构化抽取 JSON（来自 `raw_llm_response`）+ 编译后的 .md，不需要原始 `extracted_text`。

### 2.4 数据迁移（现有 32 篇论文）

```sql
-- 在桌面端 Phase 1 migration 中执行
-- 假设当前桌面用户 ID 是 :user_uuid
UPDATE papers SET user_id = :user_uuid WHERE user_id IS NULL;
UPDATE papers SET id = uuid_v4()::text WHERE id IS NULL;  -- 触发新 UUID
```

实际 INT→UUID 转换需要保留映射表 `papers_id_remap (legacy_id INT, new_id UUID)` 给依赖 paper.id 的外键（knowledge_nodes.source_paper_ids）做重写。

---

## 3. `knowledge_nodes` 表

### 3.1 Postgres 等价

```sql
CREATE TABLE knowledge_nodes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    node_type       TEXT DEFAULT 'concept',     -- paper/technique/dataset/problem/concept/entity
    node_origin     TEXT DEFAULT 'auto',        -- auto/manual

    -- 概念精选生命周期
    promotion_status        TEXT DEFAULT 'pending',  -- pending/promoted/rejected
    promoted_by             TEXT,                    -- heuristic/llm/user/legacy
    promotion_reason        TEXT,
    last_promotion_eval_at  TIMESTAMPTZ,

    -- 历史字段
    hidden                  BOOLEAN DEFAULT FALSE,   -- 已废弃，迁移期保留

    tags                    JSONB DEFAULT '[]'::jsonb,
    embedding               JSONB,                    -- vector(1536) on Phase 4 if pgvector
    source_paper_ids        JSONB DEFAULT '[]'::jsonb,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    legacy_id               INTEGER
);

CREATE INDEX knowledge_nodes_user_id_idx ON knowledge_nodes (user_id);
CREATE INDEX knowledge_nodes_user_type_idx ON knowledge_nodes (user_id, node_type);
CREATE INDEX knowledge_nodes_user_status_idx ON knowledge_nodes (user_id, promotion_status);

ALTER TABLE knowledge_nodes ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON knowledge_nodes
  FOR ALL USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
```

### 3.2 embedding 存储

v1：用 `JSONB` 存 1536 维 float 数组（与 SQLite 兼容）
v2：迁到 `pgvector` extension，启用 `vector(1536)` + IVFFlat 索引，
快速相似度查询（移动端 Ask 拉相关概念时受益）。

### 3.3 source_paper_ids 重写

`source_paper_ids` 当前存 INT 数组，迁移时必须改写为 UUID 数组：

```sql
UPDATE knowledge_nodes SET source_paper_ids = (
  SELECT jsonb_agg(papers_id_remap.new_id)
  FROM jsonb_array_elements_text(source_paper_ids) AS old(id)
  JOIN papers_id_remap ON papers_id_remap.legacy_id = old.id::int
);
```

---

## 4. `knowledge_edges` 表

### 4.1 Postgres 等价

```sql
CREATE TABLE knowledge_edges (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

    source_id       UUID NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
    target_id       UUID NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
    relation_type   TEXT DEFAULT 'related',
    weight          DOUBLE PRECISION DEFAULT 0.0,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    legacy_id       INTEGER,

    -- 同一对节点 + 关系类型 + user 唯一
    UNIQUE (user_id, source_id, target_id, relation_type)
);

CREATE INDEX knowledge_edges_user_source_idx ON knowledge_edges (user_id, source_id);
CREATE INDEX knowledge_edges_user_target_idx ON knowledge_edges (user_id, target_id);

ALTER TABLE knowledge_edges ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON knowledge_edges
  FOR ALL USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
```

### 4.2 source_id / target_id 的 user_id 一致性

外键引用 `knowledge_nodes(id)`，但不能保证两端的 user_id 与自己 user_id 一致。
RLS 已经在读层面阻断跨用户访问，但要补一个 INSERT trigger 做双重校验：

```sql
CREATE OR REPLACE FUNCTION check_edge_user_consistency() RETURNS TRIGGER AS $$
BEGIN
  IF (SELECT user_id FROM knowledge_nodes WHERE id = NEW.source_id) != NEW.user_id THEN
    RAISE EXCEPTION 'source node user mismatch';
  END IF;
  IF (SELECT user_id FROM knowledge_nodes WHERE id = NEW.target_id) != NEW.user_id THEN
    RAISE EXCEPTION 'target node user mismatch';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER edge_user_consistency_check
  BEFORE INSERT OR UPDATE ON knowledge_edges
  FOR EACH ROW EXECUTE FUNCTION check_edge_user_consistency();
```

---

## 5. `llm_calls` 表

**决策：不同步到云**。

理由：
- 桌面端的 LLM 调用大量走 Codex CLI（本地）；云端没有视角
- 云端的 Ask 调用是另一个数据源，不需要混在一起
- 隐私：包含 task / model / token 等 telemetry，没必要传出本机

**云端会另起一张 `cloud_llm_calls` 表**，仅记录 `/api/cloud/ask` 的调用：

```sql
CREATE TABLE cloud_llm_calls (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    called_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    task            TEXT NOT NULL,             -- 'ask_mobile' / 'ask_synthesis_mobile' / ...
    provider        TEXT NOT NULL,             -- 'openai'
    model           TEXT NOT NULL,
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    total_tokens    INTEGER,
    latency_ms      INTEGER,
    success         BOOLEAN DEFAULT TRUE,
    error_class     TEXT
    -- ⚠️ 不存 user 的 OpenAI key、不存 prompt 内容、不存 response 内容
);

CREATE INDEX cloud_llm_calls_user_time_idx ON cloud_llm_calls (user_id, called_at DESC);

ALTER TABLE cloud_llm_calls ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON cloud_llm_calls
  FOR ALL USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
```

用途：移动端在云端"使用统计"页展示自己的 Ask 调用次数 / token 消耗。

---

## 6. 新增的云端表

### 6.1 `users` profile

Supabase Auth 提供 `auth.users` 内置表；我们添加一张可读的 profile 表用于扩展：

```sql
CREATE TABLE user_profiles (
    user_id             UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    display_name        TEXT,
    desktop_first_seen  TIMESTAMPTZ,           -- 第一次有桌面同步的时间
    last_desktop_sync_at TIMESTAMPTZ,
    last_mobile_open_at  TIMESTAMPTZ,
    settings             JSONB DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON user_profiles
  FOR ALL USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
```

### 6.2 `sync_state` 同步水位线

```sql
CREATE TABLE sync_state (
    user_id              UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    device_id            TEXT NOT NULL,           -- 桌面端在首次启动时生成 UUID
    last_pushed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_push_revision   BIGINT NOT NULL DEFAULT 0,
    pending_tables       JSONB DEFAULT '[]'::jsonb,  -- 推送失败的表名

    PRIMARY KEY (user_id, device_id)
);

ALTER TABLE sync_state ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON sync_state
  FOR ALL USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
```

桌面端 `device_id` 在首次 Tauri 启动时生成（macOS keychain / Windows credential store 持久化）。

### 6.3 `wiki_files` 文件元数据

实际 `.md` 文件存 Supabase Storage（路径如 `wiki/{user_id}/papers/0001-foo.md`），
但元数据需要可查询：

```sql
CREATE TABLE wiki_files (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

    kind            TEXT NOT NULL,                -- 'paper' / 'concept' / 'index' / 'lint_report'
    rel_path        TEXT NOT NULL,                -- 'papers/0001-foo.md'
    storage_path    TEXT NOT NULL,                -- 'wiki/{user_id}/papers/0001-foo.md'
    content_hash    TEXT NOT NULL,                -- sha256 of content; idempotency 检测
    size_bytes      INTEGER NOT NULL,

    -- frontmatter 缓存（避免每次拉 .md 解析）
    title           TEXT,
    aliases         JSONB DEFAULT '[]'::jsonb,
    compiled_at     TIMESTAMPTZ,

    -- 关联
    paper_id        UUID REFERENCES papers(id) ON DELETE SET NULL,
    concept_id      UUID REFERENCES knowledge_nodes(id) ON DELETE SET NULL,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (user_id, rel_path)
);

CREATE INDEX wiki_files_user_kind_idx ON wiki_files (user_id, kind);

ALTER TABLE wiki_files ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON wiki_files
  FOR ALL USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
```

### 6.4 `wiki_search` FTS（可选）

如果要在云上支持 wiki 全文搜索（移动端搜索时用），加：

```sql
CREATE INDEX wiki_files_title_fts ON wiki_files
  USING GIN (to_tsvector('simple', coalesce(title, '')));
```

Phase 4 再启用，v1 移动端搜索可以走 wiki_files.title 简单 LIKE。

---

## 7. 现有数据迁移流程

桌面端 Phase 1 启动时一次性执行：

```text
1. 创建 papers_id_remap (legacy_id INT, new_id UUID) 临时表
2. 创建 nodes_id_remap (legacy_id INT, new_id UUID) 临时表
3. 创建 edges_id_remap (legacy_id INT, new_id UUID) 临时表

4. 对每张表：
   a. 添加 user_id, legacy_id 列
   b. UPDATE 行：legacy_id = id; id = uuid_generate_v4(); user_id = :current_user_uuid
   c. 把 (legacy_id, new_id) 映射写入 remap 表

5. 对 knowledge_nodes.source_paper_ids 做 INT→UUID 映射重写
6. 对 knowledge_edges.source_id/target_id 做 INT→UUID 映射重写

7. 对所有 .md 文件：
   a. 计算 content_hash
   b. 上传到 Storage：wiki/{user_id}/{rel_path}
   c. 在 wiki_files 表登记

8. 标记 sync_state.last_pushed_at = NOW()
9. 删除 remap 临时表（保留 6 个月用于回查）
```

预计耗时：
- 32 papers + 523 nodes + 3635 edges = 4190 行 SQL update
- 82 + 32 = 114 个 .md 文件上传
- 实际跑应在 5 分钟内完成

---

## 8. 验收 checklist

Phase 0 结束前需要验证：

- [ ] 在本地 `supabase start` 起的 PG 上能 apply 所有 migration 不报错
- [ ] 用 user A 插入 papers，用 user B 的 JWT SELECT 不返回任何行（RLS 生效）
- [ ] 用 user A 尝试 INSERT 一行 user_id=B 的 papers 行，被拒绝
- [ ] knowledge_edges 跨 user 的 source/target 在 trigger 阶段被拒绝
- [ ] 现有 32 篇论文能成功跑完迁移脚本到 staging Postgres
- [ ] 迁移后 `SELECT COUNT(*) FROM ...` 与本地 SQLite 完全一致
