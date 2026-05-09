import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ChevronDown,
  ChevronUp,
  CheckCircle2,
  AlertTriangle,
  Loader2,
  Sparkles,
  FileText,
  BookMarked,
  Files,
} from 'lucide-react'
import {
  getWikiFreshness,
  getWikiStatus,
  recompileAllConcepts,
  recompileAllPaperPages,
  type WikiCompileState,
  type WikiFreshnessSummary,
} from '../api/client'

const POLL_INTERVAL_MS = 1500

type StageTone = 'ok' | 'warning' | 'pending' | 'running'

/**
 * Horizontal pipeline of the wiki-compile lifecycle, anchored above the
 * graph view. Each stage card collapses three pieces of information into
 * one row:
 *
 *   - "where am I" (status icon + count)
 *   - "what's wrong" (missing / stale / orphan deltas, when present)
 *   - "what can I do" (inline compile CTA when actionable)
 *
 * The bar polls wiki status while a compile is running so the user gets
 * live progress without leaving the graph.
 */
export default function PipelineStatusBar({
  onCompileFinished,
}: {
  onCompileFinished?: () => void
}) {
  const [freshness, setFreshness] = useState<WikiFreshnessSummary | null>(null)
  const [status, setStatus] = useState<WikiCompileState | null>(null)
  // Default collapsed: full 3-card grid is heavy and most of the time the
  // user only wants to glance at "is anything red". One-line summary
  // covers that; expand on demand for actions.
  const [collapsed, setCollapsed] = useState(true)
  const [submitting, setSubmitting] = useState<'papers' | 'concepts' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const wasRunningRef = useRef(false)

  const refreshFreshness = useCallback(async () => {
    try {
      setFreshness(await getWikiFreshness())
    } catch (e) {
      console.error('PipelineStatusBar freshness load failed', e)
    }
  }, [])

  useEffect(() => {
    void refreshFreshness()
  }, [refreshFreshness])

  // Status polling. Cheap (just one GET) and only matters during compile,
  // but we leave it on so external triggers (e.g. /process recompile path)
  // are also reflected here.
  useEffect(() => {
    let cancelled = false
    const tick = async () => {
      try {
        const s = await getWikiStatus()
        if (cancelled) return
        setStatus(s)
        if (wasRunningRef.current && !s.running) {
          // Compile just finished — refresh freshness + bubble up so the
          // graph can reload too.
          void refreshFreshness()
          onCompileFinished?.()
        }
        wasRunningRef.current = s.running
      } catch (e) {
        // Status endpoint can briefly 5xx during heavy compile work; just
        // skip this tick.
        if (cancelled) return
      }
    }
    void tick()
    const id = setInterval(tick, POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [refreshFreshness, onCompileFinished])

  const runningKind = status?.running ? status.kind : null

  const handleCompile = useCallback(
    async (kind: 'papers' | 'concepts') => {
      setSubmitting(kind)
      setError(null)
      try {
        if (kind === 'papers') await recompileAllPaperPages()
        else await recompileAllConcepts()
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setSubmitting(null)
      }
    },
    [],
  )

  // --- per-stage data -------------------------------------------------

  const papersStage = (() => {
    const total = freshness?.papers.total_processed ?? 0
    return {
      icon: <FileText size={13} />,
      label: '论文',
      tone: total > 0 ? ('ok' as StageTone) : ('pending' as StageTone),
      headline: total > 0 ? `${total} 已处理` : '尚未处理',
      sub: '在「论文」页扫描并处理 PDF',
      cta: null,
    }
  })()

  const paperPagesStage = (() => {
    const f = freshness?.papers
    const ok = f?.ok ?? 0
    const total = f?.total_processed ?? 0
    const missing = f?.missing_count ?? 0
    const stale = f?.stale_count ?? 0
    const orphan = f?.orphan_count ?? 0
    const issues = missing + stale + orphan
    const isRunning = runningKind === 'papers'
    const tone: StageTone = isRunning
      ? 'running'
      : total === 0
        ? 'pending'
        : issues > 0
          ? 'warning'
          : 'ok'
    return {
      icon: <Files size={13} />,
      label: '论文页',
      tone,
      headline:
        total === 0
          ? '尚未编译'
          : issues > 0
            ? `${ok} / ${total} 已就绪`
            : `${ok} / ${total} 完整`,
      sub: issuesSummary({ missing, stale, orphan }),
      cta:
        total > 0 && (issues > 0 || total !== ok)
          ? {
              label: missing > 0 || total === 0 ? '编译' : '重编译',
              onClick: () => handleCompile('papers'),
              disabled: submitting !== null || isRunning,
              busy: submitting === 'papers' || isRunning,
            }
          : null,
    }
  })()

  const conceptPagesStage = (() => {
    const f = freshness?.concepts
    const ok = f?.ok ?? 0
    const total = f?.total_nodes ?? 0
    const missing = f?.missing_count ?? 0
    const stale = f?.stale_count ?? 0
    const orphan = f?.orphan_count ?? 0
    const issues = missing + stale + orphan
    const isRunning = runningKind === 'concepts'
    const tone: StageTone = isRunning
      ? 'running'
      : total === 0
        ? 'pending'
        : issues > 0
          ? 'warning'
          : 'ok'
    return {
      icon: <BookMarked size={13} />,
      label: '概念页',
      tone,
      headline:
        total === 0
          ? '尚无可发布概念'
          : issues > 0
            ? `${ok} / ${total} 已就绪`
            : `${ok} / ${total} 完整`,
      sub: issuesSummary({ missing, stale, orphan }),
      cta:
        total > 0 && (issues > 0 || total !== ok)
          ? {
              label: missing === total ? '编译' : '重编译',
              onClick: () => handleCompile('concepts'),
              disabled: submitting !== null || isRunning,
              busy: submitting === 'concepts' || isRunning,
            }
          : null,
    }
  })()

  // --- render ---------------------------------------------------------

  // Compact one-line summary when collapsed.
  const compactStages = [papersStage, paperPagesStage, conceptPagesStage]

  return (
    <div className="border-b border-slate-800/80 bg-[#0d1016]">
      <header className="px-6 pt-2 pb-2 flex items-center gap-3">
        <Sparkles size={12} className="text-indigo-300" />
        <span className="text-[11px] tracking-wider uppercase text-slate-400 font-semibold">
          知识管道
        </span>
        {collapsed && (
          <div className="flex items-center gap-2 text-[11.5px] ml-2 flex-wrap">
            {compactStages.map((s, i) => {
              const palette = stagePalette(s.tone)
              const stageIndex = ['①', '②', '③'][i] || `${i + 1}.`
              return (
                <span
                  key={i}
                  className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md border ${palette.border} ${palette.bg}`}
                >
                  <span className="text-[10.5px] font-mono tabular-nums text-indigo-300/90">
                    {stageIndex}
                  </span>
                  <span className={palette.icon}>{s.icon}</span>
                  <span className="text-slate-200 font-medium">{s.label}</span>
                  <span className="text-slate-400 tabular-nums">{s.headline}</span>
                  {s.cta && (
                    <button
                      onClick={s.cta.onClick}
                      disabled={s.cta.disabled}
                      className={`ml-0.5 text-[10.5px] px-1.5 py-0 rounded ${palette.cta} border disabled:opacity-50 transition-colors inline-flex items-center gap-1`}
                    >
                      {s.cta.busy ? (
                        <Loader2 size={10} className="animate-spin" />
                      ) : null}
                      {s.cta.busy ? '编译中' : s.cta.label}
                    </button>
                  )}
                </span>
              )
            })}
          </div>
        )}
        <button
          onClick={() => setCollapsed(c => !c)}
          className="ml-auto text-slate-500 hover:text-slate-200 p-1 rounded-md hover:bg-slate-800/60"
          title={collapsed ? '展开' : '收起'}
        >
          {collapsed ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
        </button>
      </header>

      {!collapsed && (
        <div className="px-6 pb-3">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <StageCard index="①" stage={papersStage} />
            <StageCard
              index="②"
              stage={paperPagesStage}
              progress={runningKind === 'papers' ? status : null}
            />
            <StageCard
              index="③"
              stage={conceptPagesStage}
              progress={runningKind === 'concepts' ? status : null}
            />
          </div>

          {error && (
            <div className="mt-2 px-3 py-1.5 rounded-lg border border-rose-500/40 bg-rose-500/10 text-[11px] text-rose-200">
              {error}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function issuesSummary({
  missing,
  stale,
  orphan,
}: {
  missing: number
  stale: number
  orphan: number
}): string {
  const parts: string[] = []
  if (missing > 0) parts.push(`${missing} 待编译`)
  if (stale > 0) parts.push(`${stale} 已过期`)
  if (orphan > 0) parts.push(`${orphan} 孤儿`)
  return parts.join(' · ')
}

function StageCard({
  index,
  stage,
  progress,
}: {
  index: string
  stage: {
    icon: React.ReactNode
    label: string
    tone: StageTone
    headline: string
    sub: string
    cta: { label: string; onClick: () => void; disabled?: boolean; busy?: boolean } | null
  }
  progress?: WikiCompileState | null
}) {
  const palette = stagePalette(stage.tone)
  const pct =
    progress && progress.total > 0
      ? Math.min(100, Math.round((progress.done / progress.total) * 100))
      : 0
  return (
    <div
      className={`relative overflow-hidden rounded-xl border ${palette.border} ${palette.bg} px-3 py-2.5`}
    >
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-mono tabular-nums text-slate-500">{index}</span>
        <span className={`inline-flex items-center gap-1 ${palette.icon}`}>{stage.icon}</span>
        <span className="text-[12.5px] font-medium text-slate-200">{stage.label}</span>
        <span className="ml-auto inline-flex items-center gap-1">
          <StageIcon tone={stage.tone} />
        </span>
      </div>
      <div className="mt-1 text-[12px] text-slate-100 font-medium tabular-nums">
        {stage.headline}
      </div>
      {stage.sub && (
        <div className="mt-0.5 text-[11px] text-slate-400 leading-relaxed">{stage.sub}</div>
      )}
      {stage.cta && (
        <div className="mt-2">
          <button
            onClick={stage.cta.onClick}
            disabled={stage.cta.disabled}
            className={`inline-flex items-center gap-1.5 text-[11px] px-2.5 py-1 rounded-md border ${palette.cta} disabled:opacity-50 transition-colors`}
          >
            {stage.cta.busy ? (
              <Loader2 size={11} className="animate-spin" />
            ) : (
              <Sparkles size={11} />
            )}
            {stage.cta.busy ? '编译中…' : stage.cta.label}
          </button>
        </div>
      )}
      {progress && progress.running && (
        <>
          <div className="absolute inset-x-0 bottom-0 h-1 bg-slate-800/70">
            <div
              className="h-full bg-indigo-400 transition-all duration-300"
              style={{ width: `${Math.max(pct, 4)}%` }}
            />
          </div>
          <div className="mt-1.5 text-[11px] text-indigo-200/80 tabular-nums">
            {progress.done} / {progress.total} · {progress.errors} 失败
          </div>
        </>
      )}
    </div>
  )
}

function StageIcon({ tone }: { tone: StageTone }) {
  switch (tone) {
    case 'ok':
      return <CheckCircle2 size={12} className="text-emerald-400" />
    case 'warning':
      return <AlertTriangle size={12} className="text-amber-400" />
    case 'running':
      return <Loader2 size={12} className="animate-spin text-indigo-400" />
    case 'pending':
    default:
      return <span className="text-[11px] text-slate-500">○</span>
  }
}

function stagePalette(tone: StageTone) {
  switch (tone) {
    case 'ok':
      return {
        border: 'border-emerald-500/30',
        bg: 'bg-emerald-500/[0.04]',
        icon: 'text-emerald-300',
        cta: 'bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-200 border-emerald-500/40',
        dot: 'bg-emerald-400',
      }
    case 'warning':
      return {
        border: 'border-amber-500/30',
        bg: 'bg-amber-500/[0.05]',
        icon: 'text-amber-300',
        cta: 'bg-amber-500/15 hover:bg-amber-500/25 text-amber-200 border-amber-500/40',
        dot: 'bg-amber-400',
      }
    case 'running':
      return {
        border: 'border-indigo-500/30',
        bg: 'bg-indigo-500/[0.05]',
        icon: 'text-indigo-300',
        cta: 'bg-indigo-500/15 hover:bg-indigo-500/25 text-indigo-200 border-indigo-500/40',
        dot: 'bg-indigo-400',
      }
    case 'pending':
    default:
      return {
        border: 'border-slate-800',
        bg: 'bg-slate-900/40',
        icon: 'text-slate-400',
        cta: 'bg-slate-800 hover:bg-slate-700 text-slate-300 border-slate-700',
        dot: 'bg-slate-600',
      }
  }
}
