import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Sparkles,
  Loader2,
  Eye,
  EyeOff,
  ShieldCheck,
  Trash2,
  Zap,
  ChevronDown,
  ChevronUp,
  Check,
  Hand,
  Bot,
  Clock,
  ArrowRight,
  Pencil,
} from 'lucide-react'
import {
  acceptLLMProposals,
  getPromotionCounts,
  getPromotionPrompt,
  runPromotion,
  type PromotionRunResponse,
  type PromotionSummary,
} from '../api/client'
import PromotionPromptEditor from './PromotionPromptEditor'

type CandidateMode = 'off' | 'pending' | 'all'

interface Props {
  candidateMode: CandidateMode
  onCandidateModeChange: (mode: CandidateMode) => void
  onOpenRescue: () => void
  onPromotionRunFinished: () => void
  /** Outer view kind. Promotion controls are only meaningful for the
   *  structured node graph; on compiled-wiki swim-lane and the flat
   *  concept list the panel auto-collapses to a header strip so it
   *  doesn't dominate either view. */
  viewKind?: 'graph' | 'compiled' | 'concepts'
}

/**
 * Floating control surface anchored bottom-left of the graph view. Replaces
 * the old "flat row of buttons" layout with a state-machine style panel
 * that always answers two questions: *what just happened?* and *what
 * should I do next?*
 *
 * Layout, top → bottom:
 *   - status header   (current lifecycle phase + last-eval timestamp)
 *   - count chips     (pending / promoted / rejected + human / agent split)
 *   - phase ① 自动评审
 *   - phase ② 抽查
 *   - phase ③ 收尾   (with the suggested next-step ring around the active CTA)
 *   - transient toast (5s after a run completes)
 */
export default function CandidatePanel({
  candidateMode,
  onCandidateModeChange,
  onOpenRescue,
  onPromotionRunFinished,
  viewKind = 'graph',
}: Props) {
  const [summary, setSummary] = useState<PromotionSummary | null>(null)
  // Default collapsed so the floating panel doesn't dominate the graph
  // canvas. Header strip alone gives state + counts; expand to act.
  const [collapsed, setCollapsed] = useState(true)
  const [running, setRunning] = useState(false)
  const [accepting, setAccepting] = useState(false)
  const [useLlm, setUseLlm] = useState(true)
  const [forceAll, setForceAll] = useState(false)
  const [lastRun, setLastRun] = useState<PromotionRunResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Whether the user has a non-empty promotion prompt configured. When
  // empty the LLM stage is skipped server-side, so we surface that state
  // next to the "调用 Agent" checkbox so the user knows clicking 自动剔除
  // will only run heuristic.
  const [promptConfigured, setPromptConfigured] = useState<boolean | null>(null)
  const [promptEditorOpen, setPromptEditorOpen] = useState(false)

  const refreshPrompt = useCallback(async () => {
    try {
      const data = await getPromotionPrompt()
      setPromptConfigured(data.prompt.trim().length > 0)
    } catch (e) {
      console.warn('Failed to load promotion prompt', e)
    }
  }, [])

  useEffect(() => {
    void refreshPrompt()
  }, [refreshPrompt])

  const refresh = useCallback(async () => {
    try {
      const data = await getPromotionCounts()
      setSummary(data.summary)
    } catch (e) {
      console.error('Failed to refresh promotion counts', e)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // Compiled-graph and concept-list views are read/browse focused, so the
  // promotion controls aren't urgent — collapse back to the header strip
  // when the user toggles in. We don't auto-re-expand on switch back —
  // the user can hit the chevron when they're ready to act.
  useEffect(() => {
    if (viewKind === 'compiled' || viewKind === 'concepts') setCollapsed(true)
  }, [viewKind])

  // 5-second auto-dismiss toast that surfaces "run finished / accept finished"
  // — keeps the success signal alive long enough to be noticed but doesn't
  // require a click to dismiss.
  const flashToast = useCallback((text: string) => {
    setToast(text)
    if (toastTimerRef.current) clearTimeout(toastTimerRef.current)
    toastTimerRef.current = setTimeout(() => setToast(null), 5000)
  }, [])

  useEffect(() => {
    return () => {
      if (toastTimerRef.current) clearTimeout(toastTimerRef.current)
    }
  }, [])

  const handleRun = useCallback(async () => {
    setRunning(true)
    setError(null)
    try {
      const result = await runPromotion({ force_all: forceAll, use_llm: useLlm })
      setLastRun(result)
      setSummary(result.summary)
      onPromotionRunFinished()
      const llmPart =
        result.llm && !('error' in result.llm)
          ? ` · Agent 保留 ${result.llm.promoted} / 剔除 ${result.llm.rejected}`
          : result.llm && 'error' in result.llm
            ? ` · Agent 跳过`
            : ''
      flashToast(
        `✓ 自动剔除完成：启发式 保留 ${result.heuristic.promoted} / 剔除 ${result.heuristic.rejected}${llmPart}`,
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setRunning(false)
    }
  }, [forceAll, useLlm, onPromotionRunFinished, flashToast])

  const handleAccept = useCallback(async () => {
    setAccepting(true)
    setError(null)
    try {
      const result = await acceptLLMProposals()
      setSummary(result.summary)
      onPromotionRunFinished()
      flashToast(`✓ 已锁定 Agent 的 ${result.locked} 个剔除结果为 human 确定`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setAccepting(false)
    }
  }, [onPromotionRunFinished, flashToast])

  // Lifecycle state — drives both the header summary line and the
  // suggested-next-step highlight ring on the action CTA.
  const phase = useMemo<'empty' | 'stale' | 'needs_triage' | 'all_locked'>(() => {
    if (!summary) return 'empty'
    if (summary.total_candidates === 0) return 'empty'
    const { counts, by } = summary
    if (counts.pending > 0) return 'stale'
    if ((by.llm || 0) > 0) return 'needs_triage'
    return 'all_locked'
  }, [summary])

  const headerStatus = useMemo(() => {
    if (!summary) return { icon: '○', text: '加载中…', tone: 'text-slate-500' }
    switch (phase) {
      case 'empty':
        return { icon: '○', text: '暂无候选', tone: 'text-slate-500' }
      case 'stale':
        return {
          icon: '○',
          text: `候选待剔除 ${summary.counts.pending}`,
          tone: 'text-amber-300',
        }
      case 'needs_triage':
        return {
          icon: '◐',
          text: `Agent 剔除结果待确认 ${summary.by.llm}`,
          tone: 'text-indigo-300',
        }
      case 'all_locked':
        return { icon: '✓', text: '剔除已锁定', tone: 'text-emerald-300' }
    }
  }, [summary, phase])

  const lastEvalText = useMemo(() => {
    if (!summary?.last_eval_at) return '尚未剔除'
    return `上次剔除：${relativeTime(summary.last_eval_at)}`
  }, [summary])

  const suggestion = useMemo(() => {
    switch (phase) {
      case 'stale':
        return '建议：先点 ① 自动剔除'
      case 'needs_triage':
        return '建议：切到 ② 仅候选节点 抽查 Agent 判断 → 满意后点 ③ 确认 Agent 剔除'
      case 'all_locked':
        return '✓ 完成，可前往 Wiki 重编译概念页'
      default:
        return null
    }
  }, [phase])

  // Phase highlight: which CTA gets the ring. State machine maps to one of
  // three keys; consumers only need exact match.
  const suggestedCta: 'run' | 'accept' | null =
    phase === 'stale' ? 'run' : phase === 'needs_triage' ? 'accept' : null

  // Ultra-compact mode: compiled-graph view + collapsed = bare header
  // strip only (no timestamp / counts / suggestion). Click chevron to
  // expand back to the full panel even in compiled mode.
  const ultraCompact =
    (viewKind === 'compiled' || viewKind === 'concepts') && collapsed

  return (
    <div
      className={`absolute bottom-3 left-3 z-20 ${
        ultraCompact ? 'w-auto' : 'w-[24rem] max-w-[42vw]'
      } bg-slate-900/92 backdrop-blur rounded-xl border border-slate-800 shadow-xl shadow-black/40 text-slate-200`}
    >
      <header
        className={`flex items-center gap-2 ${
          ultraCompact ? 'px-3 py-1.5' : 'px-4 pt-3 pb-2'
        }`}
      >
        <Sparkles size={ultraCompact ? 11 : 13} className="text-indigo-300" />
        <span
          className={`font-semibold tracking-[0.08em] text-slate-300 ${
            ultraCompact ? 'text-[11px]' : 'text-[14px]'
          }`}
        >
          概念精选
        </span>
        <span
          className={`ml-auto flex items-center gap-1 ${headerStatus.tone} ${
            ultraCompact ? 'text-[11px]' : 'text-[12px]'
          }`}
        >
          <span className="font-mono">{headerStatus.icon}</span>
          <span>{headerStatus.text}</span>
        </span>
        <button
          onClick={() => setCollapsed(c => !c)}
          className="text-slate-500 hover:text-slate-200 p-1 rounded-md hover:bg-slate-800/60"
          title={collapsed ? '展开' : '收起'}
        >
          {collapsed ? (
            <ChevronUp size={ultraCompact ? 12 : 14} />
          ) : (
            <ChevronDown size={14} />
          )}
        </button>
      </header>

      {/* timestamp + counts + suggestion are hidden in ultra-compact mode
          to leave the compiled-graph canvas as much room as possible. */}
      {!ultraCompact && (
        <>
          <div className="px-4 pb-2 flex items-center gap-2 text-[12px] text-slate-500">
            <Clock size={11} />
            <span>{lastEvalText}</span>
          </div>

          <div className="px-4 pb-2 flex items-center gap-x-2.5 gap-y-1.5 text-[12px] flex-wrap">
            <CountChip label="候选" value={summary?.counts.pending ?? 0} tone="amber" />
            <CountChip label="选中" value={summary?.counts.promoted ?? 0} tone="emerald" />
            <CountChip label="淘汰" value={summary?.counts.rejected ?? 0} tone="rose" />
            <span className="text-slate-700">·</span>
            <CountChip
              icon={<Hand size={10} />}
              label="human"
              value={summary?.by.user ?? 0}
              tone="slate"
            />
            <CountChip
              icon={<Bot size={10} />}
              label="agent"
              value={summary?.by.llm ?? 0}
              tone="slate"
            />
          </div>

          {suggestion && (
            <div className="mx-4 mb-3 px-3 py-2 rounded-lg bg-indigo-500/10 border border-indigo-500/30 text-[12px] text-indigo-200 flex items-center gap-1.5 leading-relaxed">
              <ArrowRight size={12} />
              <span>{suggestion}</span>
            </div>
          )}
        </>
      )}

      {!collapsed && (
        <>
          <PhaseSection
            index="①"
            title="剔除无效知识节点"
            description="用启发式 + Agent 自动剔除不应进入图谱的无效节点：标题过短、纯数字、单篇论文私有命名、过宽研究领域词等。"
          >
            <div className="flex flex-wrap gap-2 items-center">
              <CtaButton
                onClick={handleRun}
                busy={running}
                highlight={suggestedCta === 'run'}
                tone="indigo"
                icon={<Zap size={12} />}
                busyLabel="剔除中…"
                title="对所有候选节点跑启发式 + Agent 剔除"
              >
                自动剔除
              </CtaButton>
              <button
                onClick={() => setPromptEditorOpen(true)}
                title="编辑发给 Agent 的剔除提示词；留空则只跑启发式"
                className="inline-flex min-h-9 items-center gap-1.5 text-[12px] font-medium px-3 py-2 rounded-lg border border-slate-700 bg-slate-900 hover:bg-slate-800 text-slate-300 transition-colors"
              >
                <Pencil size={12} />
                编辑提示词
              </button>
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-2 text-[12px] text-slate-400 mt-2.5 leading-relaxed">
              <label
                className="inline-flex items-center gap-1.5 cursor-pointer"
                title={
                  promptConfigured === false
                    ? '提示词未配置 → 即使勾上 Agent 也会被跳过（点编辑提示填写）'
                    : '不勾的话只跑启发式，速度快但灰色地带保留为待修订'
                }
              >
                <input
                  type="checkbox"
                  checked={useLlm}
                  onChange={e => setUseLlm(e.target.checked)}
                  className="accent-indigo-500"
                />
                <span
                  className={
                    promptConfigured === false ? 'text-amber-300/80' : undefined
                  }
                >
                  调用 Agent
                  {promptConfigured === false ? '（未配置提示词）' : ''}
                </span>
              </label>
              <label
                className="inline-flex items-center gap-1.5 cursor-pointer"
                title="忽略 30 天冷却期，对所有候选节点重新跑剔除"
              >
                <input
                  type="checkbox"
                  checked={forceAll}
                  onChange={e => setForceAll(e.target.checked)}
                  className="accent-indigo-500"
                />
                强制重剔
              </label>
            </div>
          </PhaseSection>

          <PhaseSection
            index="②"
            title="用户修订"
            description="切换图谱里可见的节点范围。看到判定不合适的，点节点在右抽屉里手动改 promote / reject。"
          >
            <ModeToggle
              value={candidateMode}
              onChange={onCandidateModeChange}
              pendingCount={summary?.counts.pending ?? 0}
              rejectedCount={summary?.counts.rejected ?? 0}
            />
          </PhaseSection>

          <PhaseSection
            index="③"
            title="工具"
            description="把 Agent 的剔除结果锁成 human 确定，未来不再被自动剔除覆盖；或去回收站召回误删的节点。"
          >
            <div className="flex flex-wrap gap-2">
              <CtaButton
                onClick={handleAccept}
                busy={accepting}
                disabled={running || !summary?.by.llm}
                highlight={suggestedCta === 'accept'}
                tone="emerald"
                icon={<ShieldCheck size={12} />}
                busyLabel="锁定中…"
                title="把所有 Agent 的剔除结果打上 human 标记，下次自动剔除不再覆盖"
              >
                确认 Agent 剔除
                {summary?.by.llm ? ` (${summary.by.llm})` : ''}
              </CtaButton>
              <CtaButton
                onClick={onOpenRescue}
                busy={false}
                disabled={!summary?.counts.rejected}
                tone="slate"
                icon={<Trash2 size={12} />}
                title="打开回收站，按淘汰来源分组查看并召回误删节点"
              >
                召回误剔
              </CtaButton>
            </div>
          </PhaseSection>

          {(toast || error || lastRun) && (
            <div className="mx-4 mb-3 px-3 py-2.5 rounded-lg border border-slate-700 bg-slate-950/70 text-[12px] text-slate-400 leading-relaxed space-y-1">
              {toast && (
                <p className="text-emerald-300 flex items-center gap-1.5">
                  <Check size={12} /> {toast}
                </p>
              )}
              {error && <p className="text-rose-300">{error}</p>}
              {lastRun && !toast && !error && <RunSummary run={lastRun} />}
            </div>
          )}
        </>
      )}

      <PromotionPromptEditor
        open={promptEditorOpen}
        onClose={() => setPromptEditorOpen(false)}
        onSaved={prompt => setPromptConfigured(prompt.trim().length > 0)}
      />
    </div>
  )
}

function PhaseSection({
  index,
  title,
  description,
  children,
}: {
  index: string
  title: string
  /** Plain-language explanation, rendered under the title. Pulled out of
   *  a tooltip so it's discoverable without hovering. */
  description?: string
  children: React.ReactNode
}) {
  const [showDescription, setShowDescription] = useState(false)
  return (
    <section className="px-4 pb-3 pt-2.5 border-t border-slate-800/60 first:border-t-0">
      <div className="flex items-baseline gap-2 mb-1">
        <span className="text-[13px] text-indigo-300 font-mono tabular-nums font-semibold">
          {index}
        </span>
        <span className="text-[15px] text-white font-semibold tracking-tight">
          {title}
        </span>
        {description && (
          <button
            type="button"
            onClick={() => setShowDescription(s => !s)}
            className="ml-auto text-[11px] text-slate-500 hover:text-slate-300 transition-colors w-5 h-5 inline-flex items-center justify-center rounded-full border border-slate-700"
            title={showDescription ? '收起说明' : '展开说明'}
            aria-label="切换说明"
          >
            ?
          </button>
        )}
      </div>
      {description && showDescription && (
        <p className="mb-2.5 text-[12px] text-slate-400 leading-6 bg-slate-950/40 border border-slate-800/60 rounded-lg px-3 py-2">
          {description}
        </p>
      )}
      {children}
    </section>
  )
}

function CtaButton({
  onClick,
  busy,
  disabled,
  highlight,
  tone,
  icon,
  busyLabel,
  title,
  children,
}: {
  onClick: () => void
  busy: boolean
  disabled?: boolean
  highlight?: boolean
  tone: 'indigo' | 'emerald' | 'slate'
  icon: React.ReactNode
  busyLabel?: string
  title?: string
  children: React.ReactNode
}) {
  const palette = {
    indigo: 'bg-indigo-500 hover:bg-indigo-400 text-white',
    emerald:
      'bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-200 border border-emerald-500/40',
    slate: 'bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700',
  }[tone]
  const ring = highlight
    ? tone === 'indigo'
      ? 'ring-2 ring-indigo-400 ring-offset-2 ring-offset-slate-900'
      : 'ring-2 ring-emerald-400 ring-offset-2 ring-offset-slate-900'
    : ''
  return (
    <button
      onClick={onClick}
      disabled={busy || disabled}
      title={title}
      className={`inline-flex min-h-9 items-center justify-center gap-1.5 text-[12px] font-medium px-3 py-2 rounded-lg disabled:opacity-50 transition-all ${palette} ${ring}`}
    >
      {busy ? <Loader2 size={12} className="animate-spin" /> : icon}
      {busy && busyLabel ? busyLabel : children}
    </button>
  )
}

function ModeToggle({
  value,
  onChange,
  pendingCount,
  rejectedCount,
}: {
  value: CandidateMode
  onChange: (m: CandidateMode) => void
  pendingCount: number
  rejectedCount: number
}) {
  // Each option may be disabled when its underlying bucket is empty —
  // a "+ 待评" button that reveals zero pending nodes just looks like a
  // dead control, so we gray it out + tooltip-explain instead.
  const options: {
    id: CandidateMode
    label: string
    icon: React.ReactNode
    disabled: boolean
    title?: string
  }[] = [
    {
      id: 'off',
      label: '仅选中节点',
      icon: <EyeOff size={12} />,
      disabled: false,
      title: '只显示已选中（promoted）的节点',
    },
    {
      id: 'pending',
      label: `仅候选节点${pendingCount > 0 ? ` (${pendingCount})` : ''}`,
      icon: <Eye size={12} />,
      disabled: pendingCount === 0,
      title:
        pendingCount === 0
          ? '当前没有候选节点'
          : '在选中节点之上叠加 pending 候选节点',
    },
    {
      id: 'all',
      label: '全量节点',
      icon: <Eye size={12} />,
      disabled: pendingCount === 0 && rejectedCount === 0,
      title:
        pendingCount === 0 && rejectedCount === 0
          ? '当前没有候选 / 已淘汰节点'
          : '叠加 pending + rejected，显示全量节点',
    },
  ]
  return (
    <div className="flex gap-0.5 p-1 rounded-lg bg-slate-950/60 border border-slate-800">
      {options.map(opt => {
        const active = value === opt.id
        return (
          <button
            key={opt.id}
            onClick={() => onChange(opt.id)}
            disabled={opt.disabled && !active}
            title={opt.title}
            className={`flex-1 inline-flex min-h-9 items-center justify-center gap-1 text-[12px] py-2 px-2.5 rounded-md whitespace-nowrap transition-colors ${
              active
                ? 'bg-slate-800 text-white shadow-inner'
                : opt.disabled
                  ? 'text-slate-700 cursor-not-allowed'
                  : 'text-slate-500 hover:text-slate-200'
            }`}
          >
            {opt.icon}
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}

function CountChip({
  icon,
  label,
  value,
  tone,
}: {
  icon?: React.ReactNode
  label: string
  value: number
  tone: 'amber' | 'emerald' | 'rose' | 'slate'
}) {
  const palette = {
    amber: 'text-amber-200',
    emerald: 'text-emerald-200',
    rose: 'text-rose-200',
    slate: 'text-slate-300',
  }[tone]
  return (
    <span className="inline-flex items-baseline gap-1">
      {icon && <span className="text-slate-500 self-center">{icon}</span>}
      <span className="text-slate-500">{label}</span>
      <span className={`tabular-nums font-medium ${palette}`}>{value}</span>
    </span>
  )
}

function RunSummary({ run }: { run: PromotionRunResponse }) {
  const llmText = (() => {
    if (run.llm == null) return null
    if ('error' in run.llm) return `Agent 跳过：${run.llm.error}`
    return `Agent：保留 ${run.llm.promoted} · 剔除 ${run.llm.rejected} · 待修订 ${run.llm.still_ambiguous}`
  })()
  return (
    <div>
      <p>
        启发式 保留 {run.heuristic.promoted} · 剔除 {run.heuristic.rejected} · 推迟 {run.heuristic.deferred}
      </p>
      {llmText && <p className="mt-0.5">{llmText}</p>}
    </div>
  )
}

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return iso
  const diff = Date.now() - then
  if (diff < 0) return '刚刚'
  const sec = Math.floor(diff / 1000)
  if (sec < 60) return `${sec} 秒前`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min} 分钟前`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr} 小时前`
  const day = Math.floor(hr / 24)
  if (day < 30) return `${day} 天前`
  return new Date(iso).toLocaleDateString()
}
