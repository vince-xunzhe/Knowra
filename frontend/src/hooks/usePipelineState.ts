// Central polling + action surface for the [知识] page pipeline.
//
// One owner of all four lifecycle states (录入 / 筛选 / 编译 / 健检) so the
// left-rail PipelineConsole and the top NextStepBanner stay perfectly in
// sync. Without this hook each panel polled the same endpoints
// independently, which made it impossible to compute a single "下一步建议"
// because the components saw mutually stale snapshots.
//
// Polling cadence: 1.5s while anything is running, 5s otherwise. Manual
// `refresh()` jumps the next tick to "now".

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  acceptLLMProposals,
  getPromotionCounts,
  getPromotionPrompt,
  getStatus,
  getWikiFreshness,
  getWikiLintStatus,
  getWikiStatus,
  processAll,
  recompileAllConcepts,
  recompileAllPaperPages,
  runPromotion,
  scanPapers,
  type LintReportStatus,
  type PromotionRunResponse,
  type PromotionSummary,
  type WikiCompileState,
  type WikiFreshnessSummary,
} from '../api/client'

export interface ProcessingStatus {
  running: boolean
  total: number
  done: number
  errors: number
  current: string
}

export type StageId = 'ingest' | 'curate' | 'compile' | 'maintain'
export type StageTone = 'ok' | 'warning' | 'danger' | 'running' | 'idle'

export interface StageSnapshot {
  id: StageId
  /** ① / ② / ③ / ④ */
  index: string
  label: string
  /** Short tone driving the chip color. */
  tone: StageTone
  /** Headline number (e.g. "12 待处理"). */
  headline: string
  /** Optional one-liner below the headline. */
  sub?: string
  /** Whether this stage is the one the next-step suggestion is pointing at. */
  isNext: boolean
}

export interface NextStep {
  stage: StageId
  /** Short imperative label rendered inside the CTA button. */
  label: string
  /** One-sentence reason shown in the banner. */
  reason: string
  /** Tone for the banner / button. */
  tone: 'indigo' | 'amber' | 'emerald' | 'slate'
  /** Async handler. Resolves once the action returns (action may still be
   *  ongoing on the server). */
  run: () => Promise<void>
  /** True while `run()` is in-flight. */
  busy: boolean
  /** True if no human action is possible right now (everything running,
   *  no work to do, …). The banner falls back to a passive state. */
  disabled?: boolean
}

export interface PipelineActions {
  scan: () => Promise<{ new_found: number; duplicates: number; total: number; unprocessed: number }>
  process: () => Promise<void>
  runPromotionRun: (params: { use_llm: boolean; force_all: boolean }) => Promise<PromotionRunResponse>
  acceptPromotion: () => Promise<void>
  recompilePapers: () => Promise<void>
  recompileConcepts: () => Promise<void>
  /** Caller opens the lint modal; the hook just exposes "lint is the next step". */
  openLint: () => void
}

export interface PipelineState {
  freshness: WikiFreshnessSummary | null
  promotion: PromotionSummary | null
  processing: ProcessingStatus | null
  compileStatus: WikiCompileState | null
  lintStatus: LintReportStatus | null
  promotionPromptConfigured: boolean | null
  /** Number of unprocessed papers known to the backend, refreshed on scan
   *  and after every poll of `/api/status`. Not a first-class field on
   *  /status — derived as total - done while running, falls back to last
   *  scan delta otherwise. */
  unprocessedHint: number
  loading: boolean
  /** Bumps every time any of the action handlers completes successfully.
   *  Consumers can listen for it to refresh derived data (graph, etc.). */
  mutationToken: number
  stages: StageSnapshot[]
  nextStep: NextStep
  /** Force the next poll tick to fire immediately. */
  refresh: () => void
}

export interface UsePipelineStateOptions {
  /** Called after process completes (success or failure) to refresh
   *  external data, e.g. graph. */
  onMutated?: () => void
  /** Called to open the lint modal — owned by the page. */
  onOpenLint?: () => void
}

const FAST_POLL = 1500
const SLOW_POLL = 5000

export function usePipelineState({
  onMutated,
  onOpenLint,
}: UsePipelineStateOptions = {}): PipelineState & PipelineActions {
  const [freshness, setFreshness] = useState<WikiFreshnessSummary | null>(null)
  const [promotion, setPromotion] = useState<PromotionSummary | null>(null)
  const [processing, setProcessing] = useState<ProcessingStatus | null>(null)
  const [compileStatus, setCompileStatus] = useState<WikiCompileState | null>(null)
  const [lintStatus, setLintStatus] = useState<LintReportStatus | null>(null)
  const [promotionPromptConfigured, setPromotionPromptConfigured] = useState<boolean | null>(null)
  const [unprocessedHint, setUnprocessedHint] = useState<number>(0)
  const [loading, setLoading] = useState(true)
  const [mutationToken, setMutationToken] = useState(0)
  const [nextStepBusy, setNextStepBusy] = useState(false)

  // Refresh trigger — bumping it kicks the polling loop awake.
  const [refreshNonce, setRefreshNonce] = useState(0)
  const refresh = useCallback(() => setRefreshNonce(n => n + 1), [])

  // Stable refs for the latest callbacks so the polling effect doesn't
  // need to re-bind when the parent re-renders. Updated inside a layout
  // effect — React 19 lint forbids writing refs during render.
  const onMutatedRef = useRef(onMutated)
  const onOpenLintRef = useRef(onOpenLint)
  useEffect(() => {
    onMutatedRef.current = onMutated
  }, [onMutated])
  useEffect(() => {
    onOpenLintRef.current = onOpenLint
  }, [onOpenLint])

  const bumpMutation = useCallback(() => {
    setMutationToken(t => t + 1)
    onMutatedRef.current?.()
    refresh()
  }, [refresh])

  // --- polling ---------------------------------------------------------

  useEffect(() => {
    let cancelled = false

    const pollFreshness = async () => {
      try {
        const f = await getWikiFreshness()
        if (!cancelled) setFreshness(f)
      } catch (e) {
        console.warn('freshness poll failed', e)
      }
    }
    const pollPromotion = async () => {
      try {
        const r = await getPromotionCounts()
        if (!cancelled) setPromotion(r.summary)
      } catch (e) {
        console.warn('promotion poll failed', e)
      }
    }
    const pollProcessing = async () => {
      try {
        const s = await getStatus()
        if (cancelled) return
        const next: ProcessingStatus = {
          running: !!s.running,
          total: s.total ?? 0,
          done: s.done ?? 0,
          errors: s.errors ?? 0,
          current: s.current ?? '',
        }
        setProcessing(next)
        // If a run is active, the remaining work is the gap. Outside an
        // active run we keep the last scan-derived hint.
        if (next.running && next.total > next.done) {
          setUnprocessedHint(next.total - next.done)
        }
      } catch {
        // Brief 5xx during heavy compile work is expected; skip tick.
      }
    }
    const pollCompile = async () => {
      try {
        const s = await getWikiStatus()
        if (!cancelled) setCompileStatus(s)
      } catch {
        // ditto.
      }
    }
    const pollLint = async () => {
      try {
        const s = await getWikiLintStatus()
        if (!cancelled) setLintStatus(s)
      } catch {
        // ditto.
      }
    }
    const pollPrompt = async () => {
      try {
        const p = await getPromotionPrompt()
        if (!cancelled) setPromotionPromptConfigured(p.prompt.trim().length > 0)
      } catch {
        // ditto.
      }
    }

    const tick = async () => {
      await Promise.all([
        pollFreshness(),
        pollPromotion(),
        pollProcessing(),
        pollCompile(),
        pollLint(),
      ])
      if (!cancelled) setLoading(false)
    }

    // Kick off
    void tick()
    void pollPrompt()

    // Pick cadence based on whether anything is running. We re-check this
    // on every interval so the cadence adapts.
    const id = setInterval(() => {
      void tick()
    }, processing?.running || compileStatus?.running ? FAST_POLL : SLOW_POLL)

    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [refreshNonce, processing?.running, compileStatus?.running])

  // --- derived stages --------------------------------------------------

  const stages = useMemo<StageSnapshot[]>(() => {
    // ① 录入 — based on paper processing status.
    const ingestRunning = !!processing?.running
    const totalProcessed = freshness?.papers.total_processed ?? 0
    const remaining = ingestRunning
      ? Math.max(0, (processing?.total ?? 0) - (processing?.done ?? 0))
      : unprocessedHint
    const ingest: StageSnapshot = {
      id: 'ingest',
      index: '①',
      label: '录入',
      tone: ingestRunning
        ? 'running'
        : totalProcessed === 0
          ? 'warning'
          : remaining > 0
            ? 'warning'
            : 'ok',
      headline: ingestRunning
        ? `处理中 ${processing?.done}/${processing?.total}`
        : totalProcessed === 0
          ? '尚未处理论文'
          : remaining > 0
            ? `${remaining} 待处理 · ${totalProcessed} 已入库`
            : `${totalProcessed} 已入库`,
      sub: ingestRunning && processing?.errors
        ? `${processing.errors} 失败`
        : undefined,
      isNext: false,
    }

    // ② 筛选 — promotion lifecycle.
    const pending = promotion?.counts.pending ?? 0
    const llmDecided = promotion?.by.llm ?? 0
    const promoted = promotion?.counts.promoted ?? 0
    const curate: StageSnapshot = {
      id: 'curate',
      index: '②',
      label: '筛选',
      tone:
        pending > 0
          ? 'warning'
          : llmDecided > 0
            ? 'warning'
            : promoted > 0
              ? 'ok'
              : 'idle',
      headline:
        pending > 0
          ? `${pending} 待评`
          : llmDecided > 0
            ? `${llmDecided} Agent 待确认`
            : promoted > 0
              ? `${promoted} 已选中`
              : '尚无候选',
      sub: promotion
        ? `选中 ${promotion.counts.promoted} · 淘汰 ${promotion.counts.rejected}`
        : undefined,
      isNext: false,
    }

    // ③ 编译 — wiki freshness.
    const compRunning = !!compileStatus?.running
    const paperMissing = freshness?.papers.missing_count ?? 0
    const paperStale = freshness?.papers.stale_count ?? 0
    const conceptMissing = freshness?.concepts.missing_count ?? 0
    const conceptStale = freshness?.concepts.stale_count ?? 0
    const compileTotalIssues = paperMissing + paperStale + conceptMissing + conceptStale
    const compileTotalNodes =
      (freshness?.papers.total_processed ?? 0) + (freshness?.concepts.total_nodes ?? 0)
    const compile: StageSnapshot = {
      id: 'compile',
      index: '③',
      label: '编译',
      tone: compRunning
        ? 'running'
        : compileTotalNodes === 0
          ? 'idle'
          : compileTotalIssues > 0
            ? 'warning'
            : 'ok',
      headline: compRunning
        ? `编译中 ${compileStatus?.done}/${compileStatus?.total}`
        : compileTotalNodes === 0
          ? '尚未编译'
          : compileTotalIssues > 0
            ? `${compileTotalIssues} 待编译`
            : '全部就绪',
      sub:
        !compRunning && compileTotalIssues > 0
          ? [
              paperMissing + paperStale > 0
                ? `论文页 ${paperMissing + paperStale}`
                : null,
              conceptMissing + conceptStale > 0
                ? `概念页 ${conceptMissing + conceptStale}`
                : null,
            ]
              .filter(Boolean)
              .join(' · ')
          : undefined,
      isNext: false,
    }

    // ④ 健检 — lint report.
    const lintExists = !!lintStatus?.exists
    const lintModified = lintStatus?.modified_at
    // "Stale" if older than 7 days — fully heuristic on the wall clock, so
    // the user can always re-run on demand. We accept the small impurity:
    // we only re-render when freshness/lint poll changes anyway, so this
    // value is implicitly refreshed on each poll tick.
    // eslint-disable-next-line react-hooks/purity
    const lintAgeMs = lintModified ? Date.now() - new Date(lintModified).getTime() : Infinity
    const lintStale = lintExists && lintAgeMs > 7 * 24 * 3600 * 1000
    const maintain: StageSnapshot = {
      id: 'maintain',
      index: '④',
      label: '健检',
      tone: lintExists
        ? lintStale
          ? 'warning'
          : 'ok'
        : compileTotalNodes > 0
          ? 'warning'
          : 'idle',
      headline: lintExists
        ? lintStale
          ? '报告已过期'
          : '报告就绪'
        : compileTotalNodes > 0
          ? '尚未运行'
          : '暂无可检',
      sub: lintExists && lintModified
        ? `更新于 ${relativeTime(lintModified)}`
        : undefined,
      isNext: false,
    }

    // Mark which stage is the "next step" — see deriveNextStep below.
    const winner = pickNextStep({
      ingestRunning,
      compRunning,
      remaining,
      totalProcessed,
      pending,
      llmDecided,
      promoted,
      compileTotalIssues,
      compileTotalNodes,
      lintExists,
      lintStale,
    })
    const stagesArr = [ingest, curate, compile, maintain]
    return stagesArr.map(s => (s.id === winner ? { ...s, isNext: true } : s))
  }, [
    processing,
    freshness,
    promotion,
    compileStatus,
    lintStatus,
    unprocessedHint,
  ])

  // --- actions ---------------------------------------------------------

  const wrap = useCallback(
    async <T,>(fn: () => Promise<T>): Promise<T> => {
      setNextStepBusy(true)
      try {
        const result = await fn()
        bumpMutation()
        return result
      } finally {
        setNextStepBusy(false)
      }
    },
    [bumpMutation],
  )

  const scan = useCallback(async () => {
    const result = await scanPapers()
    setUnprocessedHint(result.unprocessed)
    refresh()
    return result
  }, [refresh])

  const process = useCallback(async () => {
    await wrap(async () => {
      await processAll()
    })
  }, [wrap])

  const runPromotionRun = useCallback(
    async (params: { use_llm: boolean; force_all: boolean }) => {
      return wrap(async () => {
        const r = await runPromotion(params)
        setPromotion(r.summary)
        return r
      })
    },
    [wrap],
  )

  const acceptPromotion = useCallback(async () => {
    await wrap(async () => {
      const r = await acceptLLMProposals()
      setPromotion(r.summary)
    })
  }, [wrap])

  // Optimistically flip compileStatus to "running" the instant the user
  // clicks, instead of waiting up to SLOW_POLL (5s) for the next
  // /wiki/status poll. Without this, the compile buttons stay clickable
  // for several seconds after a click (looked like nothing happened).
  // The real poll then takes over with live progress and eventually
  // reports running:false when done.
  const optimisticCompiling = useCallback((kind: 'papers' | 'concepts') => {
    setCompileStatus(prev => ({
      running: true,
      kind,
      total: prev?.total ?? 0,
      done: 0,
      errors: 0,
      current: '',
      started_at: new Date().toISOString(),
      finished_at: null,
      last_error: null,
      model: prev?.model ?? null,
    }))
  }, [])

  const recompilePapers = useCallback(async () => {
    optimisticCompiling('papers')
    await wrap(async () => {
      await recompileAllPaperPages()
    })
  }, [wrap, optimisticCompiling])

  const recompileConcepts = useCallback(async () => {
    optimisticCompiling('concepts')
    await wrap(async () => {
      await recompileAllConcepts()
    })
  }, [wrap, optimisticCompiling])

  const openLint = useCallback(() => {
    onOpenLintRef.current?.()
  }, [])

  // --- nextStep CTA wiring --------------------------------------------

  const nextStep = useMemo<NextStep>(() => {
    const ingestRunning = !!processing?.running
    const compRunning = !!compileStatus?.running
    const remaining = ingestRunning
      ? Math.max(0, (processing?.total ?? 0) - (processing?.done ?? 0))
      : unprocessedHint
    const totalProcessed = freshness?.papers.total_processed ?? 0
    const pending = promotion?.counts.pending ?? 0
    const llmDecided = promotion?.by.llm ?? 0
    const promoted = promotion?.counts.promoted ?? 0
    const paperMissing = freshness?.papers.missing_count ?? 0
    const paperStale = freshness?.papers.stale_count ?? 0
    const conceptMissing = freshness?.concepts.missing_count ?? 0
    const conceptStale = freshness?.concepts.stale_count ?? 0
    const paperIssues = paperMissing + paperStale
    const conceptIssues = conceptMissing + conceptStale
    const compileTotalNodes =
      (freshness?.papers.total_processed ?? 0) + (freshness?.concepts.total_nodes ?? 0)
    const lintExists = !!lintStatus?.exists
    const lintModified = lintStatus?.modified_at
    // Same wall-clock heuristic as in `stages`; see comment there.
    const lintStale = lintExists && lintModified
      // eslint-disable-next-line react-hooks/purity
      ? Date.now() - new Date(lintModified).getTime() > 7 * 24 * 3600 * 1000
      : false

    // 1) Active run — nothing to suggest, just show progress.
    if (ingestRunning) {
      return {
        stage: 'ingest',
        label: `处理中 ${processing?.done}/${processing?.total}`,
        reason: '正在解析论文，编译会在结束后自动接力。',
        tone: 'indigo',
        run: async () => {},
        busy: true,
        disabled: true,
      }
    }
    if (compRunning) {
      return {
        stage: 'compile',
        label: `编译中 ${compileStatus?.done}/${compileStatus?.total}`,
        reason: '正在写入 wiki .md，请稍候。',
        tone: 'indigo',
        run: async () => {},
        busy: true,
        disabled: true,
      }
    }
    // 2) Unprocessed papers — feed the pipeline first.
    if (remaining > 0 || totalProcessed === 0) {
      const knownUnprocessed = remaining > 0
      return {
        stage: 'ingest',
        label: knownUnprocessed ? `处理 ${remaining} 篇论文` : '扫描并处理论文',
        reason: knownUnprocessed
          ? `有 ${remaining} 篇新论文待解析，先把它们入库再做后续步骤。`
          : '当前还没有处理过的论文，先扫描目录并处理论文。',
        tone: 'indigo',
        // scan() returns a result object — discard it so the union widens to
        // Promise<void> like every other branch.
        run: knownUnprocessed ? process : async () => { await scan() },
        busy: nextStepBusy,
      }
    }
    // 3) Pending promotion candidates — curate next.
    if (pending > 0) {
      return {
        stage: 'curate',
        label: `评审 ${pending} 个候选`,
        reason: `有 ${pending} 个待评候选节点，建议先跑「自动剔除」筛掉无效条目。`,
        tone: 'amber',
        run: () => runPromotionRun({ use_llm: true, force_all: false }).then(() => {}),
        busy: nextStepBusy,
      }
    }
    // 4) Agent decisions pending human confirmation.
    if (llmDecided > 0) {
      return {
        stage: 'curate',
        label: `确认 ${llmDecided} 个 Agent 判断`,
        reason: `Agent 已给出 ${llmDecided} 个剔除判断，确认后下次自动剔除不会再覆盖。`,
        tone: 'amber',
        run: acceptPromotion,
        busy: nextStepBusy,
      }
    }
    // 5) Wiki recompile.
    if (paperIssues > 0) {
      return {
        stage: 'compile',
        label: `编译 ${paperIssues} 个论文页`,
        reason: `有 ${paperIssues} 个论文页待编译或已过期。`,
        tone: 'amber',
        run: recompilePapers,
        busy: nextStepBusy,
      }
    }
    if (conceptIssues > 0) {
      return {
        stage: 'compile',
        label: `编译 ${conceptIssues} 个概念页`,
        reason: `有 ${conceptIssues} 个概念页待编译或已过期。`,
        tone: 'amber',
        run: recompileConcepts,
        busy: nextStepBusy,
      }
    }
    // 6) Lint health-check.
    if (compileTotalNodes > 0 && (!lintExists || lintStale)) {
      return {
        stage: 'maintain',
        label: lintExists ? '重新运行健康检查' : '运行健康检查',
        reason: lintExists
          ? '已编译内容有过更新，健康检查报告需要刷新。'
          : '已编译概念 ≥ 1，建议跑一次健康检查找出短桩 / 可合并项。',
        tone: 'amber',
        run: async () => {
          openLint()
        },
        busy: nextStepBusy,
      }
    }
    if (promoted === 0 && compileTotalNodes === 0) {
      return {
        stage: 'ingest',
        label: '扫描目录',
        reason: '从零开始：先扫描论文目录。',
        tone: 'indigo',
        run: async () => { await scan() },
        busy: nextStepBusy,
      }
    }
    // 7) All clean.
    return {
      stage: 'maintain',
      label: '全部就绪 · 去提问',
      reason: '所有阶段都已完成，可以打开 Ask 提问或继续添加论文。',
      tone: 'emerald',
      run: async () => {},
      busy: false,
      disabled: true,
    }
  }, [
    processing,
    compileStatus,
    unprocessedHint,
    freshness,
    promotion,
    lintStatus,
    nextStepBusy,
    process,
    scan,
    runPromotionRun,
    acceptPromotion,
    recompilePapers,
    recompileConcepts,
    openLint,
  ])

  return {
    freshness,
    promotion,
    processing,
    compileStatus,
    lintStatus,
    promotionPromptConfigured,
    unprocessedHint,
    loading,
    mutationToken,
    stages,
    nextStep,
    refresh,
    scan,
    process,
    runPromotionRun,
    acceptPromotion,
    recompilePapers,
    recompileConcepts,
    openLint,
  }
}

// Pure helper — keeps the stage isNext logic and nextStep CTA logic in
// the same ordering. Returns the StageId of the stage that should glow.
function pickNextStep(c: {
  ingestRunning: boolean
  compRunning: boolean
  remaining: number
  totalProcessed: number
  pending: number
  llmDecided: number
  promoted: number
  compileTotalIssues: number
  compileTotalNodes: number
  lintExists: boolean
  lintStale: boolean
}): StageId {
  if (c.ingestRunning) return 'ingest'
  if (c.compRunning) return 'compile'
  if (c.remaining > 0 || c.totalProcessed === 0) return 'ingest'
  if (c.pending > 0 || c.llmDecided > 0) return 'curate'
  if (c.compileTotalIssues > 0) return 'compile'
  if (c.compileTotalNodes > 0 && (!c.lintExists || c.lintStale)) return 'maintain'
  return 'maintain'
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
