import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  X,
  Loader2,
  Sparkles,
  Send,
  ChevronDown,
  ChevronRight,
  Copy,
  RefreshCw,
  Pin,
  AlertTriangle,
} from 'lucide-react'
import {
  askWiki,
  createSynthesisConcept,
  getWikiIndexStatus,
  rebuildWikiIndex,
  type AskResponse,
  type AskTraceStep,
  type WikiIndexStatus,
} from '../api/client'

interface Props {
  open: boolean
  onClose: () => void
  /** Called after the user successfully files an answer back as a
   *  synthesis concept page — host can refresh the graph / counts. */
  onSynthesisCreated?: (conceptId: number) => void
}

interface Turn {
  role: 'user' | 'assistant'
  content: string
  trace?: AskTraceStep[]
  citedFiles?: string[]
  durationMs?: number
  steps?: number
  model?: string
}

/**
 * Cross-wiki Q&A surface. The user types a question; the backend agent
 * uses tool-calls (list_wiki_index / search_wiki / read_wiki) to gather
 * context and returns a markdown answer. The trace is folded by default
 * so the answer is the protagonist; expand to debug what the agent did.
 *
 * Conversation state is local — closing the drawer keeps history alive
 * for the session, opening it again restores it. A "清空" button resets
 * if the user wants a fresh thread.
 */
export default function AskDrawer({ open, onClose, onSynthesisCreated }: Props) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [question, setQuestion] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [indexStatus, setIndexStatus] = useState<WikiIndexStatus | null>(null)
  const [rebuilding, setRebuilding] = useState(false)
  const [synthesisDraft, setSynthesisDraft] = useState<{
    body: string
    citedFiles: string[]
    sourceQuestion: string
  } | null>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  // Refresh index status whenever the drawer opens — cheap GET, gives the
  // user immediate feedback on whether the agent has its primary scaffolding
  // (index.md) ready.
  useEffect(() => {
    if (!open) return
    getWikiIndexStatus().then(setIndexStatus).catch(() => setIndexStatus(null))
    requestAnimationFrame(() => inputRef.current?.focus())
  }, [open])

  // Auto-scroll to bottom on new turn — common chat affordance.
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [turns, submitting])

  const handleSend = useCallback(async () => {
    const q = question.trim()
    if (!q || submitting) return

    const history = turns.map(t => ({ role: t.role, content: t.content }))
    setTurns(prev => [...prev, { role: 'user', content: q }])
    setQuestion('')
    setSubmitting(true)
    setError(null)
    try {
      const result: AskResponse = await askWiki(q, history)
      setTurns(prev => [
        ...prev,
        {
          role: 'assistant',
          content: result.answer,
          trace: result.trace,
          citedFiles: result.cited_files,
          durationMs: result.duration_ms,
          steps: result.steps,
          model: result.model,
        },
      ])
    } catch (e: unknown) {
      const msg =
        // axios shape — pull `detail` if backend raised HTTPException
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (e instanceof Error ? e.message : String(e))
      setError(msg)
    } finally {
      setSubmitting(false)
      requestAnimationFrame(() => inputRef.current?.focus())
    }
  }, [question, submitting, turns])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      // Cmd/Ctrl+Enter sends; plain Enter still does newlines so prompts
      // can be multi-line.
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault()
        void handleSend()
      }
    },
    [handleSend],
  )

  const handleClear = useCallback(() => {
    setTurns([])
    setError(null)
  }, [])

  const handleRebuildIndex = useCallback(async () => {
    setRebuilding(true)
    setError(null)
    try {
      await rebuildWikiIndex()
      const fresh = await getWikiIndexStatus()
      setIndexStatus(fresh)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setRebuilding(false)
    }
  }, [])

  if (!open) return null

  const indexMissing = indexStatus !== null && !indexStatus.exists

  return (
    <div className="fixed inset-0 z-40 flex pointer-events-none">
      {/* Click-away backdrop. Soft because the drawer doesn't fully cover
          the graph behind it — user might want to compare while asking. */}
      <div
        className="flex-1 bg-black/30 pointer-events-auto"
        onClick={onClose}
      />
      <aside className="w-[40rem] max-w-[60vw] h-full bg-[#0d1016] border-l border-slate-800 flex flex-col pointer-events-auto shadow-[0_0_60px_rgba(0,0,0,0.6)]">
        <header className="px-5 py-3 border-b border-slate-800/80 flex items-center gap-2">
          <Sparkles size={13} className="text-indigo-300" />
          <span className="text-[10px] tracking-[0.12em] uppercase text-indigo-300/70 font-mono">
            知识库
          </span>
          <span className="text-slate-700">/</span>
          <h2 className="text-[13px] font-semibold text-white tracking-tight">
            Ask
          </h2>
          {turns.length > 0 && (
            <span className="text-[10.5px] text-slate-500">
              · {turns.filter(t => t.role === 'user').length} 轮对话
            </span>
          )}
          <button
            onClick={handleClear}
            disabled={turns.length === 0}
            className="ml-auto text-[10.5px] px-1.5 py-0.5 rounded text-slate-500 hover:text-slate-200 hover:bg-slate-800/60 disabled:opacity-40 transition-colors"
            title="清空当前会话历史"
          >
            清空
          </button>
          <button
            onClick={onClose}
            className="text-slate-500 hover:text-slate-200 p-1 rounded hover:bg-slate-800/60 transition-colors"
            title="关闭"
          >
            <X size={13} />
          </button>
        </header>

        {/* index.md status strip */}
        <div className="px-5 py-1.5 border-b border-slate-800/70 flex items-center gap-2 text-[10.5px] text-slate-500">
          {indexStatus === null ? (
            <span className="text-slate-600">索引状态加载中…</span>
          ) : indexMissing ? (
            <>
              <AlertTriangle size={11} className="text-amber-400" />
              <span className="text-amber-200">
                index.md 未生成，agent 会跳过总览这一步
              </span>
              <button
                onClick={handleRebuildIndex}
                disabled={rebuilding}
                className="ml-auto inline-flex items-center gap-1 text-[10.5px] px-1.5 py-0.5 rounded border border-amber-500/40 bg-amber-500/10 hover:bg-amber-500/20 text-amber-200 disabled:opacity-50 transition-colors"
              >
                {rebuilding ? <Loader2 size={9} className="animate-spin" /> : <RefreshCw size={9} />}
                生成索引
              </button>
            </>
          ) : indexStatus.stale ? (
            <>
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
              <span className="text-amber-200">
                索引过期 · {indexStatus.indexed_papers ?? '?'} → {indexStatus.current_papers ?? '?'} 论文 ·{' '}
                {indexStatus.indexed_concepts ?? '?'} → {indexStatus.current_concepts ?? '?'} 概念
              </span>
              <button
                onClick={handleRebuildIndex}
                disabled={rebuilding}
                className="ml-auto inline-flex items-center gap-1 text-[10.5px] px-1.5 py-0.5 rounded border border-amber-500/40 bg-amber-500/10 hover:bg-amber-500/20 text-amber-200 disabled:opacity-50 transition-colors"
                title="知识库内容自上次索引后变了，建议重建以让 agent 看到全部新条目"
              >
                {rebuilding ? <Loader2 size={9} className="animate-spin" /> : <RefreshCw size={9} />}
                重建索引
              </button>
            </>
          ) : (
            <>
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
              <span>
                index.md · {(indexStatus.size / 1024).toFixed(1)} KB ·{' '}
                {indexStatus.current_papers ?? 0} 论文 ·{' '}
                {indexStatus.current_concepts ?? 0} 概念
              </span>
              <button
                onClick={handleRebuildIndex}
                disabled={rebuilding}
                className="ml-auto inline-flex items-center gap-1 text-[10.5px] text-slate-500 hover:text-slate-200 disabled:opacity-50 transition-colors"
                title="重新生成索引（LLM 全量改写）"
              >
                {rebuilding ? <Loader2 size={9} className="animate-spin" /> : <RefreshCw size={9} />}
                重建索引
              </button>
            </>
          )}
        </div>

        {/* Conversation scroll area */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-5 py-4 space-y-5">
          {turns.length === 0 && !submitting && (
            <EmptyHint />
          )}
          {turns.map((turn, i) => {
            const lastUserAbove =
              turn.role === 'assistant'
                ? [...turns.slice(0, i)].reverse().find(t => t.role === 'user')?.content || ''
                : ''
            return (
              <TurnView
                key={i}
                turn={turn}
                onFileBack={
                  turn.role === 'assistant'
                    ? () =>
                        setSynthesisDraft({
                          body: turn.content,
                          citedFiles: turn.citedFiles || [],
                          sourceQuestion: lastUserAbove,
                        })
                    : undefined
                }
              />
            )
          })}
          {submitting && <ThinkingIndicator />}
          {error && (
            <div className="px-3 py-2 rounded-lg border border-rose-500/40 bg-rose-500/10 text-[12px] text-rose-200">
              {error}
            </div>
          )}
        </div>

        {synthesisDraft && (
          <SynthesisSaveModal
            draft={synthesisDraft}
            onClose={() => setSynthesisDraft(null)}
            onSaved={(conceptId) => {
              setSynthesisDraft(null)
              onSynthesisCreated?.(conceptId)
            }}
          />
        )}

        {/* Composer */}
        <footer className="px-5 py-3 border-t border-slate-800/70">
          <div className="rounded-xl border border-slate-800 bg-slate-950/60 focus-within:border-indigo-500/60 transition-colors">
            <textarea
              ref={inputRef}
              value={question}
              onChange={e => setQuestion(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="问点什么 — 比如：MASt3R 和 DUSt3R 的关系？跨论文比较 X 方法 …"
              rows={3}
              className="w-full resize-none bg-transparent px-3 py-2 text-[12.5px] leading-6 text-slate-100 placeholder-slate-600 focus:outline-none"
              spellCheck={false}
            />
            <div className="px-3 py-1.5 border-t border-slate-800/70 flex items-center gap-2">
              <span className="text-[10px] text-slate-600 tabular-nums">
                {question.length} 字 · ⌘ Enter 发送
              </span>
              <button
                onClick={handleSend}
                disabled={submitting || question.trim().length === 0}
                className="ml-auto inline-flex items-center gap-1.5 text-[11.5px] font-medium px-3 py-1 rounded-md bg-indigo-500 hover:bg-indigo-400 text-white disabled:opacity-50 transition-colors"
              >
                {submitting ? (
                  <Loader2 size={11} className="animate-spin" />
                ) : (
                  <Send size={11} />
                )}
                发送
              </button>
            </div>
          </div>
        </footer>
      </aside>
    </div>
  )
}

function EmptyHint() {
  const examples = [
    'MASt3R 和 DUSt3R 在匹配任务上有什么区别？',
    '我的库里哪些论文用 InfoNCE？',
    '总结所有关于 3D Gaussian 的概念',
    '哪些数据集出现在 ≥3 篇论文里？',
  ]
  return (
    <div className="text-center text-slate-500 py-10 px-4">
      <Sparkles size={18} className="mx-auto text-indigo-400/70 mb-3" />
      <p className="text-[12.5px] mb-1">问任何关于这个知识库的问题</p>
      <p className="text-[11px] text-slate-600 mb-4 leading-relaxed">
        Agent 会先读 index 找方向，再 search / read 具体 .md 综合答复。
      </p>
      <div className="flex flex-col items-center gap-1.5">
        {examples.map(e => (
          <span
            key={e}
            className="inline-block text-[11px] text-slate-500 italic"
          >
            「{e}」
          </span>
        ))}
      </div>
    </div>
  )
}

function ThinkingIndicator() {
  return (
    <div className="flex items-center gap-2 text-[11px] text-slate-500">
      <Loader2 size={11} className="animate-spin text-indigo-300" />
      <span>Agent 正在读取知识库…</span>
    </div>
  )
}

function TurnView({
  turn,
  onFileBack,
}: {
  turn: Turn
  onFileBack?: () => void
}) {
  if (turn.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-xl bg-indigo-500/15 border border-indigo-500/30 px-3 py-2 text-[12.5px] text-indigo-100 whitespace-pre-wrap">
          {turn.content}
        </div>
      </div>
    )
  }
  return <AssistantTurn turn={turn} onFileBack={onFileBack} />
}

function AssistantTurn({
  turn,
  onFileBack,
}: {
  turn: Turn
  onFileBack?: () => void
}) {
  const [traceOpen, setTraceOpen] = useState(false)
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(turn.content)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // clipboard not available — silent
    }
  }, [turn.content])

  return (
    <div className="space-y-2">
      {/* Trace folder */}
      {turn.trace && turn.trace.length > 0 && (
        <div className="rounded-lg border border-slate-800 bg-slate-950/40">
          <button
            onClick={() => setTraceOpen(o => !o)}
            className="w-full flex items-center gap-1.5 px-2.5 py-1 text-[10.5px] text-slate-500 hover:text-slate-300 transition-colors"
          >
            {traceOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
            <span>工具调用 · {turn.trace.length} 步</span>
            {turn.durationMs != null && (
              <span className="ml-auto tabular-nums text-slate-600">
                {(turn.durationMs / 1000).toFixed(1)}s
                {turn.model ? ` · ${turn.model}` : ''}
              </span>
            )}
          </button>
          {traceOpen && (
            <ul className="px-2.5 pb-2 space-y-1 text-[10.5px] text-slate-400 leading-relaxed">
              {turn.trace.map((t, i) => (
                <li key={i} className="flex gap-2">
                  <span className="text-slate-600 tabular-nums w-5">
                    {i + 1}.
                  </span>
                  <span className="font-mono text-indigo-300/80 shrink-0">
                    {t.tool}
                  </span>
                  <span className="text-slate-500 truncate">
                    {summarizeArgs(t.args)} → {t.result_summary}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Answer body */}
      <div className="rounded-xl border border-slate-800 bg-slate-900/40 px-4 py-3">
        <div className="markdown-notes max-w-none text-[12.5px] leading-7 text-slate-100">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{turn.content}</ReactMarkdown>
        </div>
        <div className="mt-3 pt-2 border-t border-slate-800/60 flex items-center gap-2 text-[10.5px] text-slate-500">
          {turn.citedFiles && turn.citedFiles.length > 0 && (
            <span className="truncate">
              📚 {turn.citedFiles.length} 处引用
            </span>
          )}
          <button
            onClick={handleCopy}
            className="ml-auto inline-flex items-center gap-1 px-1.5 py-0.5 rounded hover:bg-slate-800/60 hover:text-slate-200 transition-colors"
          >
            <Copy size={10} />
            {copied ? '已复制' : '复制'}
          </button>
          {onFileBack && (
            <button
              onClick={onFileBack}
              className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded border border-emerald-500/40 bg-emerald-500/10 text-emerald-200 hover:bg-emerald-500/20 transition-colors"
              title="把这个答案存为概念页，下次自动剔除会把它纳入图谱"
            >
              <Pin size={10} />
              存为概念页
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function summarizeArgs(args: Record<string, unknown>): string {
  const entries = Object.entries(args)
  if (entries.length === 0) return ''
  return entries
    .map(([k, v]) => `${k}=${typeof v === 'string' ? `"${v}"` : JSON.stringify(v)}`)
    .join(', ')
    .slice(0, 80)
}

function SynthesisSaveModal({
  draft,
  onClose,
  onSaved,
}: {
  draft: { body: string; citedFiles: string[]; sourceQuestion: string }
  onClose: () => void
  onSaved: (conceptId: number) => void
}) {
  // Auto-derive a default title from the question — first ~30 chars
  // makes a reasonable concept-page heading. User can edit before save.
  const defaultTitle = draft.sourceQuestion.replace(/[?？\s]+$/, '').slice(0, 30)
  const [title, setTitle] = useState(defaultTitle)
  const [tagsInput, setTagsInput] = useState('synthesis')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  // Pull paper IDs from cited files like "data/wiki/papers/0009-...md"
  // and "paper:9". Used as the source list on the new concept node.
  const sourcePaperIds = useMemo(() => {
    const ids = new Set<number>()
    for (const f of draft.citedFiles) {
      let m = /\/papers\/(\d+)-/.exec(f)
      if (!m) m = /^paper:(\d+)$/.exec(f)
      if (m) ids.add(parseInt(m[1], 10))
    }
    return [...ids]
  }, [draft.citedFiles])

  const handleSave = useCallback(async () => {
    if (!title.trim() || saving) return
    setSaving(true)
    setErr(null)
    try {
      const result = await createSynthesisConcept({
        title: title.trim(),
        body: draft.body,
        source_question: draft.sourceQuestion,
        source_paper_ids: sourcePaperIds,
        tags: tagsInput
          .split(',')
          .map(t => t.trim())
          .filter(Boolean),
      })
      onSaved(result.concept_id)
    } catch (e: unknown) {
      const msg =
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        (e instanceof Error ? e.message : String(e))
      setErr(msg)
    } finally {
      setSaving(false)
    }
  }, [title, tagsInput, draft, sourcePaperIds, saving, onSaved])

  return (
    <div className="absolute inset-0 z-10 bg-black/55 backdrop-blur-sm flex items-center justify-center p-6">
      <div className="w-full max-w-md bg-[#0d1016] border border-slate-800 rounded-xl shadow-2xl flex flex-col">
        <header className="px-4 py-3 border-b border-slate-800 flex items-center gap-2">
          <Pin size={12} className="text-emerald-300" />
          <h3 className="text-[13px] font-semibold text-white">存为概念页</h3>
          <button
            onClick={onClose}
            className="ml-auto text-slate-500 hover:text-slate-200 p-1 rounded hover:bg-slate-800/60"
          >
            <X size={13} />
          </button>
        </header>
        <div className="px-4 py-3 space-y-2.5">
          <div>
            <label className="text-[10.5px] text-slate-500">标题</label>
            <input
              value={title}
              onChange={e => setTitle(e.target.value)}
              placeholder="给这个综合答案起个名字"
              className="mt-1 w-full px-2.5 py-1.5 text-[12px] bg-slate-950 border border-slate-800 rounded-md text-slate-200 placeholder-slate-600 focus:outline-none focus:border-slate-700"
            />
          </div>
          <div>
            <label className="text-[10.5px] text-slate-500">
              标签（逗号分隔）
            </label>
            <input
              value={tagsInput}
              onChange={e => setTagsInput(e.target.value)}
              placeholder="synthesis, …"
              className="mt-1 w-full px-2.5 py-1.5 text-[12px] bg-slate-950 border border-slate-800 rounded-md text-slate-200 placeholder-slate-600 focus:outline-none focus:border-slate-700"
            />
          </div>
          <div className="text-[10.5px] text-slate-500 leading-relaxed">
            将创建 <code className="text-slate-400">data/wiki/concepts/{`{id}-{slug}`}.md</code>，
            origin = <code className="text-slate-400">manual</code>，并自动 promoted。
            来源问题 + {sourcePaperIds.length} 篇引用论文会写入 frontmatter。
          </div>
          {err && (
            <div className="px-2 py-1.5 text-[11px] rounded border border-rose-500/40 bg-rose-500/10 text-rose-200">
              {err}
            </div>
          )}
        </div>
        <footer className="px-4 py-2.5 border-t border-slate-800 flex items-center gap-2">
          <button
            onClick={onClose}
            className="text-[11.5px] px-2.5 py-1 rounded text-slate-400 hover:text-slate-200 hover:bg-slate-800/60 transition-colors"
          >
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={!title.trim() || saving}
            className="ml-auto inline-flex items-center gap-1.5 text-[11.5px] font-medium px-2.5 py-1 rounded bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-200 border border-emerald-500/40 disabled:opacity-50 transition-colors"
          >
            {saving ? <Loader2 size={11} className="animate-spin" /> : <Pin size={11} />}
            存为概念页
          </button>
        </footer>
      </div>
    </div>
  )
}
