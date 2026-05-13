import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  X,
  Loader2,
  Sparkles,
  Send,
  Plus,
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
  type SynthesisConceptResult,
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

interface AskSession {
  id: string
  title: string
  turns: Turn[]
  question: string
  createdAt: string
  updatedAt: string
}

type SynthesisScope = 'turn' | 'session'

interface SynthesisDraft {
  sessionTitle: string
  sessionTurns: Turn[]
}

interface DuplicateConceptConflict {
  message: string
  concept_id: number
  title: string
  filename: string
  path: string
  reason?: string | null
}

const ASK_DRAWER_STORAGE_KEY = 'knowra:ask-drawer:sessions'

interface PersistedAskState {
  sessions: AskSession[]
  activeSessionId: string | null
}

function isPristineSession(session: AskSession): boolean {
  return session.turns.length === 0 && session.question.trim().length === 0
}

function normalizeSessions(sessions: AskSession[]): AskSession[] {
  const normalized: AskSession[] = []
  let keptPristine = false
  for (const session of sessions) {
    if (isPristineSession(session)) {
      if (keptPristine) continue
      keptPristine = true
    }
    normalized.push(session)
  }
  return normalized
}

function createAskSession(title = '新对话'): AskSession {
  const now = new Date().toISOString()
  return {
    id: `ask-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    title,
    turns: [],
    question: '',
    createdAt: now,
    updatedAt: now,
  }
}

function hydrateSession(raw: unknown): AskSession | null {
  if (!raw || typeof raw !== 'object') return null
  const value = raw as Partial<AskSession>
  const id = typeof value.id === 'string' ? value.id : ''
  if (!id) return null
  const now = new Date().toISOString()
  return {
    id,
    title: typeof value.title === 'string' && value.title.trim() ? value.title : '新对话',
    turns: Array.isArray(value.turns) ? value.turns : [],
    question: typeof value.question === 'string' ? value.question : '',
    createdAt: typeof value.createdAt === 'string' ? value.createdAt : now,
    updatedAt: typeof value.updatedAt === 'string' ? value.updatedAt : now,
  }
}

function loadPersistedState(): PersistedAskState {
  if (typeof window === 'undefined') {
    const session = createAskSession()
    return { sessions: [session], activeSessionId: session.id }
  }
  try {
    const raw = window.localStorage.getItem(ASK_DRAWER_STORAGE_KEY)
    if (!raw) {
      const session = createAskSession()
      return { sessions: [session], activeSessionId: session.id }
    }
    const parsed = JSON.parse(raw) as Partial<PersistedAskState>
    const hydratedSessions = Array.isArray(parsed.sessions)
      ? parsed.sessions
        .map(hydrateSession)
        .filter((session): session is AskSession => session !== null)
      : []
    const sessions = normalizeSessions(hydratedSessions)
    if (sessions.length === 0) {
      const session = createAskSession()
      return { sessions: [session], activeSessionId: session.id }
    }
    const activeSessionId =
      typeof parsed.activeSessionId === 'string'
      && sessions.some(session => session.id === parsed.activeSessionId)
        ? parsed.activeSessionId
        : sessions[0].id
    return {
      sessions,
      activeSessionId,
    }
  } catch {
    const session = createAskSession()
    return { sessions: [session], activeSessionId: session.id }
  }
}

interface AnsweredRound {
  question: string
  answer: Turn
}

function buildAnsweredRounds(turns: Turn[]): AnsweredRound[] {
  const rounds: AnsweredRound[] = []
  let pendingQuestion = ''
  for (const turn of turns) {
    if (turn.role === 'user') {
      pendingQuestion = turn.content.trim()
      continue
    }
    rounds.push({
      question: pendingQuestion,
      answer: turn,
    })
  }
  return rounds.filter(round => round.answer.content.trim())
}

function trimAskAnswerHeading(markdown: string): string {
  return markdown.replace(/^\s*#{1,6}\s*(answer|回答)\s*\n+/i, '').trim()
}

function buildSessionSynthesisBody(rounds: AnsweredRound[]): string {
  const finalRound = rounds[rounds.length - 1]
  if (!finalRound) return ''

  const parts: string[] = [
    `> 由 Ask 多轮会话整理而成，共 ${rounds.length} 轮问答。`,
  ]

  const questions = rounds
    .map(round => round.question)
    .filter(Boolean)
  if (questions.length > 0) {
    parts.push([
      '## 问题演进',
      '',
      ...questions.map((question, index) => `${index + 1}. ${question}`),
    ].join('\n'))
  }

  parts.push([
    '## 最终综合结论',
    '',
    trimAskAnswerHeading(finalRound.answer.content),
  ].join('\n'))

  if (rounds.length > 1) {
    parts.push([
      '## 对话摘录',
      '',
      rounds.map((round, index) => [
        `### 第 ${index + 1} 轮`,
        '',
        '**问题**',
        '',
        round.question || '（未记录问题）',
        '',
        '**回答**',
        '',
        trimAskAnswerHeading(round.answer.content),
      ].join('\n')).join('\n\n'),
    ].join('\n'))
  }

  return parts.join('\n\n').trim()
}

function collectCitedFiles(turns: Turn[]): string[] {
  const files: string[] = []
  const seen = new Set<string>()
  for (const turn of turns) {
    for (const file of turn.citedFiles || []) {
      if (!file || seen.has(file)) continue
      seen.add(file)
      files.push(file)
    }
  }
  return files
}

function extractDuplicateConceptConflict(error: unknown): DuplicateConceptConflict | null {
  const detail = (error as {
    response?: {
      data?: {
        detail?: {
          message?: string
          duplicate_reason?: string | null
          duplicate_concept?: {
            concept_id?: number
            title?: string
            filename?: string
            path?: string
          }
        }
      }
    }
  })?.response?.data?.detail
  const duplicate = detail?.duplicate_concept
  if (
    !detail
    || !duplicate
    || typeof detail.message !== 'string'
    || typeof duplicate.concept_id !== 'number'
    || typeof duplicate.title !== 'string'
    || typeof duplicate.filename !== 'string'
    || typeof duplicate.path !== 'string'
  ) {
    return null
  }
  return {
    message: detail.message,
    concept_id: duplicate.concept_id,
    title: duplicate.title,
    filename: duplicate.filename,
    path: duplicate.path,
    reason: typeof detail.duplicate_reason === 'string' ? detail.duplicate_reason : null,
  }
}

/**
 * Cross-wiki Q&A surface. The user types a question; the backend agent
 * uses tool-calls (list_wiki_index / search_wiki / read_wiki) to gather
 * context and returns a markdown answer. The trace is folded by default
 * so the answer is the protagonist; expand to debug what the agent did.
 *
 * Conversation state is local — closing the drawer keeps history alive
 * for the session, opening it again restores it. Clearing the current
 * thread removes that saved session and falls back to another one (or a
 * new blank thread when none remain).
 */
export default function AskDrawer({ open, onClose, onSynthesisCreated }: Props) {
  const [askState, setAskState] = useState<PersistedAskState>(() => loadPersistedState())
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ tone: 'emerald' | 'amber'; text: string } | null>(null)
  const [indexStatus, setIndexStatus] = useState<WikiIndexStatus | null>(null)
  const [rebuilding, setRebuilding] = useState(false)
  const [synthesisDraft, setSynthesisDraft] = useState<SynthesisDraft | null>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const activeSession = useMemo(() => {
    const found = askState.sessions.find(session => session.id === askState.activeSessionId)
    return found || askState.sessions[0] || createAskSession()
  }, [askState])
  const turns = activeSession?.turns || []
  const question = activeSession?.question || ''
  const sessionsForSelect = useMemo(
    () => [...askState.sessions].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)),
    [askState.sessions],
  )

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

  useEffect(() => {
    setError(null)
    setNotice(null)
    setSynthesisDraft(null)
  }, [activeSession?.id])

  // GraphPage unmounts when the user switches to another top-level page, so
  // persist the Ask conversations in localStorage to preserve the session
  // list across page switches and browser restarts on this machine.
  useEffect(() => {
    if (typeof window === 'undefined') return
    try {
      window.localStorage.setItem(
        ASK_DRAWER_STORAGE_KEY,
        JSON.stringify(askState),
      )
    } catch {
      // Ignore quota / privacy-mode failures — Ask still works in-memory.
    }
  }, [askState])

  const updateSessionById = useCallback((sessionId: string, updater: (session: AskSession) => AskSession) => {
    setAskState(prev => ({
      ...prev,
      sessions: prev.sessions.map(session =>
        session.id === sessionId ? updater(session) : session,
      ),
    }))
  }, [])

  const setQuestion = useCallback((nextQuestion: string) => {
    updateSessionById(activeSession.id, session => ({
      ...session,
      question: nextQuestion,
    }))
  }, [activeSession.id, updateSessionById])

  const handleNewChat = useCallback(() => {
    if (submitting) return
    if (activeSession.turns.length === 0 && !activeSession.question.trim()) {
      requestAnimationFrame(() => inputRef.current?.focus())
      return
    }
    const session = createAskSession()
    setAskState(prev => ({
      sessions: [session, ...prev.sessions],
      activeSessionId: session.id,
    }))
    setError(null)
    requestAnimationFrame(() => inputRef.current?.focus())
  }, [activeSession.question, activeSession.turns.length, submitting])

  const handleSessionSelect = useCallback((sessionId: string) => {
    if (submitting) return
    setAskState(prev => ({
      ...prev,
      activeSessionId: sessionId,
    }))
    requestAnimationFrame(() => inputRef.current?.focus())
  }, [submitting])

  const handleSend = useCallback(async () => {
    const q = question.trim()
    if (!q || submitting) return

    const sessionId = activeSession.id
    const sentAt = new Date().toISOString()
    const history = turns.map(t => ({ role: t.role, content: t.content }))
    updateSessionById(sessionId, session => ({
      ...session,
      turns: [...session.turns, { role: 'user', content: q }],
      question: '',
      updatedAt: sentAt,
    }))
    setSubmitting(true)
    setError(null)
    setNotice(null)
    try {
      const result: AskResponse = await askWiki(q, history)
      updateSessionById(sessionId, session => ({
        ...session,
        title:
          session.title === '新对话'
          && session.turns.length === 1
          && session.turns[0]?.role === 'user'
          && (result.session_title || '').trim()
            ? (result.session_title || '').trim()
            : session.title,
        turns: [
          ...session.turns,
          {
            role: 'assistant',
            content: result.answer,
            trace: result.trace,
            citedFiles: result.cited_files,
            durationMs: result.duration_ms,
            steps: result.steps,
            model: result.model,
          },
        ],
        updatedAt: new Date().toISOString(),
      }))
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
  }, [activeSession.id, question, submitting, turns, updateSessionById])

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
    if (submitting) return
    setAskState(prev => {
      const remaining = prev.sessions.filter(session => session.id !== activeSession.id)
      const normalized = normalizeSessions(remaining)
      if (normalized.length === 0) {
        const session = createAskSession()
        return {
          sessions: [session],
          activeSessionId: session.id,
        }
      }
      return {
        sessions: normalized,
        activeSessionId: normalized[0].id,
      }
    })
    setError(null)
    setNotice(null)
  }, [activeSession.id, submitting])

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
          {askState.sessions.length > 1 && (
            <span className="text-[10.5px] text-slate-500">
              · {askState.sessions.length} 个会话
            </span>
          )}
          <button
            onClick={onClose}
            className="ml-auto text-slate-500 hover:text-slate-200 p-1 rounded hover:bg-slate-800/60 transition-colors"
            title="关闭"
          >
            <X size={13} />
          </button>
        </header>

        <div className="px-5 py-2.5 border-b border-slate-800/70 flex items-center gap-2">
          <select
            value={activeSession.id}
            onChange={e => handleSessionSelect(e.target.value)}
            disabled={submitting}
            className="min-w-0 flex-1 rounded-lg border border-slate-800 bg-slate-950/70 px-3 py-2 text-[12px] text-slate-200 focus:outline-none focus:border-indigo-500/60 disabled:opacity-50"
            title="切换已保存会话"
          >
            {sessionsForSelect.map(session => (
              <option key={session.id} value={session.id}>
                {session.title} · {session.turns.filter(turn => turn.role === 'user').length} 轮
              </option>
            ))}
          </select>
          <button
            onClick={handleNewChat}
            disabled={submitting}
            className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-500/30 bg-indigo-500/10 px-2.5 py-2 text-[11.5px] font-medium text-indigo-100 hover:bg-indigo-500/20 disabled:opacity-50 transition-colors"
            title="开启一个新的 Ask 会话"
          >
            <Plus size={11} />
            新建聊天
          </button>
          <button
            onClick={handleClear}
            disabled={submitting || (turns.length === 0 && question.trim().length === 0)}
            className="text-[10.5px] px-2 py-1 rounded text-slate-500 hover:text-slate-200 hover:bg-slate-800/60 disabled:opacity-40 transition-colors"
            title="删除当前会话"
          >
            清空
          </button>
        </div>

        <div className="px-5 py-1.5 border-b border-slate-800/70 flex items-center gap-2 text-[10.5px] text-slate-500">
          <span className="truncate">
            当前会话 · {turns.filter(t => t.role === 'user').length} 轮对话
          </span>
          <span className="text-slate-700">·</span>
          <span className="truncate">保存在本机浏览器</span>
        </div>

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
          {turns.map((turn, i) => (
            <TurnView
              key={i}
              turn={turn}
              onFileBack={
                turn.role === 'assistant'
                  ? () =>
                      setSynthesisDraft({
                        sessionTitle: activeSession.title,
                        sessionTurns: turns.slice(0, i + 1),
                      })
                  : undefined
              }
            />
          ))}
          {submitting && <ThinkingIndicator />}
          {notice && (
            <div className={`px-3 py-2 rounded-lg border text-[12px] ${
              notice.tone === 'emerald'
                ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
                : 'border-amber-500/40 bg-amber-500/10 text-amber-200'
            }`}>
              {notice.text}
            </div>
          )}
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
            onSaved={(result, scope) => {
              setSynthesisDraft(null)
              const relatedSuffix = result.related_concepts_added && result.related_concepts_added > 0
                ? `，并连到了 ${result.related_concepts_added} 个已有概念`
                : ''
              setNotice(
                result.forced_create
                  ? {
                      tone: 'amber',
                      text:
                        scope === 'session'
                          ? '你已忽略同名提示，强制创建了一份新的会话归纳概念页。'
                          : '你已忽略同名提示，强制创建了一份新的概念页。',
                    }
                  : {
                      tone: 'emerald',
                      text:
                        result.analysis_used
                          ? (
                              scope === 'session'
                                ? `已用模型把当前会话整理成概念页，并同步加入知识图谱${relatedSuffix}。`
                                : `已用模型整理当前回答并创建概念页，已加入知识图谱${relatedSuffix}。`
                            )
                          : (
                              scope === 'session'
                                ? '已把当前会话整理成概念页，并加入知识图谱。'
                                : '已创建新的概念页，并加入知识图谱。'
                            ),
                    },
              )
              onSynthesisCreated?.(result.concept_id)
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
  draft: SynthesisDraft
  onClose: () => void
  onSaved: (result: SynthesisConceptResult, scope: SynthesisScope) => void
}) {
  const rounds = useMemo(() => buildAnsweredRounds(draft.sessionTurns), [draft.sessionTurns])
  const currentRound = rounds[rounds.length - 1]
  const sessionQuestions = useMemo(
    () => rounds.map(round => round.question).filter(Boolean),
    [rounds],
  )
  const hasSessionScope = rounds.length > 1
  const [scope, setScope] = useState<SynthesisScope>('turn')
  const defaultTitles = useMemo(() => {
    const turnBase =
      currentRound?.question ||
      draft.sessionTitle ||
      'Ask归纳'
    const sessionBase =
      draft.sessionTitle && draft.sessionTitle !== '新对话'
        ? draft.sessionTitle
        : sessionQuestions[0] || currentRound?.question || 'Ask归纳'
    return {
      turn: turnBase.replace(/[?？\s]+$/, '').slice(0, 30) || 'Ask归纳',
      session: sessionBase.replace(/[?？\s]+$/, '').slice(0, 30) || 'Ask归纳',
    }
  }, [currentRound?.question, draft.sessionTitle, sessionQuestions])
  const [title, setTitle] = useState(defaultTitles.turn)
  const [tagsInput, setTagsInput] = useState('ask归纳')
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [titleTouched, setTitleTouched] = useState(false)
  const [duplicateConflict, setDuplicateConflict] = useState<DuplicateConceptConflict | null>(null)

  useEffect(() => {
    if (!hasSessionScope && scope !== 'turn') {
      setScope('turn')
    }
  }, [hasSessionScope, scope])

  useEffect(() => {
    if (titleTouched) return
    setTitle(defaultTitles[scope])
  }, [defaultTitles, scope, titleTouched])

  const selectedBody = useMemo(() => {
    if (!currentRound) return ''
    if (scope === 'session') {
      return buildSessionSynthesisBody(rounds)
    }
    return currentRound.answer.content
  }, [currentRound, rounds, scope])

  const selectedQuestions = useMemo(() => {
    if (scope === 'session') return sessionQuestions
    return currentRound?.question ? [currentRound.question] : []
  }, [currentRound?.question, scope, sessionQuestions])

  const selectedCitedFiles = useMemo(() => {
    if (scope === 'session') return collectCitedFiles(draft.sessionTurns)
    return currentRound?.answer.citedFiles || []
  }, [currentRound?.answer.citedFiles, draft.sessionTurns, scope])

  // Pull paper IDs from cited files like "data/wiki/papers/0009-...md"
  // and "paper:9". Used as the source list on the new concept node.
  const sourcePaperIds = useMemo(() => {
    const ids = new Set<number>()
    for (const f of selectedCitedFiles) {
      let m = /\/papers\/(\d+)-/.exec(f)
      if (!m) m = /^paper:(\d+)$/.exec(f)
      if (m) ids.add(parseInt(m[1], 10))
    }
    return [...ids]
  }, [selectedCitedFiles])

  const submitSave = useCallback(async (forceCreate = false) => {
    if (!title.trim() || saving || !selectedBody.trim()) return
    setSaving(true)
    setErr(null)
    if (!forceCreate) {
      setDuplicateConflict(null)
    }
    try {
      const result = await createSynthesisConcept({
        title: title.trim(),
        body: selectedBody,
        source_question: selectedQuestions[0] || '',
        source_questions: selectedQuestions,
        synthesis_scope: scope,
        force_create: forceCreate,
        source_paper_ids: sourcePaperIds,
        tags: tagsInput
          .split(',')
          .map(t => t.trim())
          .filter(Boolean),
      })
      onSaved(result, scope)
    } catch (e: unknown) {
      const duplicate = extractDuplicateConceptConflict(e)
      if (duplicate) {
        setDuplicateConflict(duplicate)
        setErr(null)
      } else {
        const msg =
          (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
          (e instanceof Error ? e.message : String(e))
        setErr(msg)
      }
    } finally {
      setSaving(false)
    }
  }, [title, selectedBody, selectedQuestions, scope, sourcePaperIds, tagsInput, saving, onSaved])

  if (!currentRound) return null

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
          {hasSessionScope && (
            <div>
              <label className="text-[10.5px] text-slate-500">保存范围</label>
              <div className="mt-1 grid grid-cols-2 gap-2">
                <button
                  onClick={() => setScope('turn')}
                  className={`rounded-md border px-2.5 py-2 text-[11.5px] transition-colors ${
                    scope === 'turn'
                      ? 'border-indigo-500/50 bg-indigo-500/15 text-indigo-100'
                      : 'border-slate-800 bg-slate-950 text-slate-400 hover:text-slate-200'
                  }`}
                >
                  当前回答
                </button>
                <button
                  onClick={() => setScope('session')}
                  className={`rounded-md border px-2.5 py-2 text-[11.5px] transition-colors ${
                    scope === 'session'
                      ? 'border-indigo-500/50 bg-indigo-500/15 text-indigo-100'
                      : 'border-slate-800 bg-slate-950 text-slate-400 hover:text-slate-200'
                  }`}
                >
                  当前会话
                </button>
              </div>
              <div className="mt-1.5 text-[10.5px] text-slate-500 leading-relaxed">
                {scope === 'session'
                  ? '会先用模型整理截至当前的多轮问答，再生成一个更像概念条目的页面，保留问题演进、最终结论和来源论文。'
                  : '会先用模型把当前回答整理成概念摘要与条目正文；前面的多轮上下文只通过这轮答案间接体现。'}
              </div>
            </div>
          )}
          <div>
            <label className="text-[10.5px] text-slate-500">标题</label>
            <input
              value={title}
              onChange={(e) => {
                setTitle(e.target.value)
                setTitleTouched(true)
                setDuplicateConflict(null)
                setErr(null)
              }}
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
              placeholder="ask归纳, …"
              className="mt-1 w-full px-2.5 py-1.5 text-[12px] bg-slate-950 border border-slate-800 rounded-md text-slate-200 placeholder-slate-600 focus:outline-none focus:border-slate-700"
            />
          </div>
          <div className="text-[10.5px] text-slate-500 leading-relaxed">
            将创建 <code className="text-slate-400">data/wiki/concepts/{`{id}-{slug}`}.md</code>，
            origin = <code className="text-slate-400">manual</code>，并自动 promoted。
            {scope === 'session' ? `本次会话的 ${selectedQuestions.length} 个问题` : '当前问题'}
            {' '}+ {sourcePaperIds.length} 篇引用论文会写入 frontmatter。
          </div>
          <div className="text-[10.5px] text-slate-500 leading-relaxed">
            如果检测到同名概念，系统会先提示现有概念；如果你确认这是误判，也可以强制新增。
          </div>
          <div className="rounded-md border border-slate-800/80 bg-slate-950/60 px-2.5 py-2 text-[10.5px] text-slate-500 leading-relaxed">
            {scope === 'session'
              ? `将整理 ${rounds.length} 轮问答，并同步生成图谱节点摘要、概念正文与来源论文列表。`
              : '将整理当前回答，生成图谱节点摘要与结构化概念正文，不会把整个 session 原样抄进去。'}
          </div>
          {duplicateConflict && (
            <div className="rounded-md border border-amber-500/40 bg-amber-500/10 px-2.5 py-2 text-[11px] text-amber-100 leading-relaxed space-y-1.5">
              <div>{duplicateConflict.message}</div>
              {duplicateConflict.reason && (
                <div className="text-amber-200/80">
                  判重依据：{duplicateConflict.reason}
                </div>
              )}
              <div>
                已有概念：<span className="font-medium">{duplicateConflict.title}</span>{' '}
                <span className="text-amber-200/80">#{duplicateConflict.concept_id}</span>
              </div>
              <div className="text-amber-200/80">
                文件：<code>{duplicateConflict.filename}</code>
              </div>
              <div className="text-amber-200/70">
                如果你确认这不是重复概念，可以点下方“仍然创建”，系统会保留两个同名概念。
              </div>
            </div>
          )}
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
          {duplicateConflict && (
            <button
              onClick={() => void submitSave(true)}
              disabled={saving || !title.trim() || !selectedBody.trim()}
              className="inline-flex items-center gap-1.5 text-[11.5px] font-medium px-2.5 py-1 rounded border border-amber-500/40 bg-amber-500/10 text-amber-100 hover:bg-amber-500/20 disabled:opacity-50 transition-colors"
            >
              {saving ? <Loader2 size={11} className="animate-spin" /> : <Pin size={11} />}
              仍然创建
            </button>
          )}
          <button
            onClick={() => void submitSave(false)}
            disabled={!title.trim() || !selectedBody.trim() || saving}
            className="ml-auto inline-flex items-center gap-1.5 text-[11.5px] font-medium px-2.5 py-1 rounded bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-200 border border-emerald-500/40 disabled:opacity-50 transition-colors"
          >
            {saving ? <Loader2 size={11} className="animate-spin" /> : <Pin size={11} />}
            {scope === 'session' ? '整理并存为概念页' : '存为概念页'}
          </button>
        </footer>
      </div>
    </div>
  )
}
