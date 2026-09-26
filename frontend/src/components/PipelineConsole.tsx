import { t as tr } from '../i18n/catalog'
import { useLocale } from '../i18n/preferences'
// Left-rail control panel for the [知识] page. Replaces the previous
// PipelineStatusBar (top) + CandidatePanel (bottom-left floating). All
// pipeline actions now live in one vertical stack of stage cards so the
// user has a single, ordered surface to scan top→bottom:
//
//   ① 录入   scan + process
//   ② 筛选   run promotion / accept agent / rescue rejected
//   ③ 编译   recompile papers / concepts
//   ④ 健检   run lint / open report
//
// The card whose `isNext` is true gets a glowing ring — driven by the
// hook's nextStep state machine — but stage bodies stay collapsed until
// the user expands them. Whole rail collapses to a thin 48-px icon strip
// for users who don't need it.

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import {
  Sparkles,
  ScanLine,
  Play,
  Zap,
  ShieldCheck,
  Trash2,
  FileText,
  BookMarked,
  Stethoscope,
  FileSearch,
  Loader2,
  PanelLeftClose,
  PanelLeftOpen,
  CheckCircle2,
  AlertTriangle,
  Eye,
  EyeOff,
  Pencil,
  Bot,
  Hand,
  CloudUpload,
  ChevronDown,
  ChevronRight,
} from 'lucide-react'
import {
  getPromotionCounts,
  getStatus,
  getWikiFreshness,
  getWikiStatus,
  revealScannedFile,
  runWikiLint,
  waitForWikiLint,
  type DuplicatePaperFile,
  type PaperScanResult,
  type WikiCompileState,
} from '../api/client'
import { getLastSyncAt } from '../api/cloud'
import { useCloudAuth } from '../hooks/useCloudAuth'
import type {
  PipelineActions,
  PipelineState,
  StageId,
  StageSnapshot,
} from '../hooks/usePipelineState'
import { gatherLocalSnapshot } from '../services/gatherLocalSnapshot'
import { runSync } from '../services/syncAgent'
import DuplicateFilesModal from './DuplicateFilesModal'
import PromotionPromptEditor from './PromotionPromptEditor'
import SyncStageCard from './SyncStageCard'

type CandidateMode = 'off' | 'pending' | 'all'
type ConsoleStageId = StageId | 'sync'
type RunAllTone = 'idle' | 'running' | 'success' | 'warning'

interface RunAllStatus {
  running: boolean
  label: string
  detail?: string
  tone: RunAllTone
}

interface Props {
  state: PipelineState & PipelineActions
  /** Candidate visibility selector — controlled by parent so the graph
   *  canvas can react. */
  candidateMode: CandidateMode
  onCandidateModeChange: (m: CandidateMode) => void
  /** Opens the lint modal. */
  onOpenLint: () => void
  /** Opens the rescue (recall rejected) modal. */
  onOpenRescue: () => void
  /** Opens the Ask drawer. */
  onOpenAsk: () => void
}

export default function PipelineConsole({
  state,
  candidateMode,
  onCandidateModeChange,
  onOpenLint,
  onOpenRescue,
  onOpenAsk,
}: Props) {
  useLocale()
  const auth = useCloudAuth()
  const [collapsed, setCollapsed] = useState(false)
  const [expandedStages, setExpandedStages] = useState<Set<ConsoleStageId>>(() => new Set())
  const [error, setError] = useState<string | null>(null)
  const [promptEditorOpen, setPromptEditorOpen] = useState(false)
  const [duplicateFiles, setDuplicateFiles] = useState<DuplicatePaperFile[]>([])
  const [duplicateModalOpen, setDuplicateModalOpen] = useState(false)
  const [scanSummary, setScanSummary] = useState<PaperScanResult | null>(null)
  const [checkingDuplicates, setCheckingDuplicates] = useState(false)
  const [duplicateCheckError, setDuplicateCheckError] = useState<string | null>(null)
  const duplicateCheckInFlight = useRef(false)
  const scanRevision = useRef(0)
  const [selectedDuplicatePath, setSelectedDuplicatePath] = useState<string | null>(null)
  // Which pipeline action is currently in-flight (null = idle). Drives
  // the grey/disabled + spinner state on the action buttons so the user
  // gets feedback and can't double-fire a long-running job (e.g. 自动剔除
  // calls the LLM and takes a while).
  const [busyKey, setBusyKey] = useState<string | null>(null)
  // Local toggles that live inside the curate stage but persist across
  // re-renders within the page.
  const [useLlm, setUseLlm] = useState(true)
  const [forceAll, setForceAll] = useState(false)
  const autoScanStartedRef = useRef(false)
  const scanForDirectory = state.scan
  const [runAllStatus, setRunAllStatus] = useState<RunAllStatus>({
    running: false,
    label: tr("全流程编排"),
    tone: 'idle',
  })

  useEffect(() => {
    if (autoScanStartedRef.current) return
    autoScanStartedRef.current = true
    void scanForDirectory().catch(() => {})
  }, [scanForDirectory])

  const toggleStage = (id: ConsoleStageId) => {
    setExpandedStages(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const openFromIconRail = (id: ConsoleStageId) => {
    setCollapsed(false)
    setExpandedStages(new Set([id]))
  }

  const setRunAllStep = (label: string, detail?: string) => {
    setRunAllStatus({ running: true, label, detail, tone: 'running' })
  }

  const showScanResult = (result: PaperScanResult) => {
    scanRevision.current += 1
    const items = result.duplicate_files || []
    setScanSummary(result)
    setDuplicateFiles(items)
    setDuplicateCheckError(null)
    setDuplicateModalOpen(items.length > 0)
    setSelectedDuplicatePath(items[0]?.path ?? null)
    if (items.length === 0) return
    const first = items[0]
    // The scan click explicitly asks the app to inspect this local folder.
    // Reveal the deterministic first duplicate immediately; the modal also
    // lets the user switch items and reveal them again.
    void revealScannedFile(first.path).catch(() => {})
  }

  const refreshDuplicateFiles = useCallback(async () => {
    if (duplicateCheckInFlight.current) return
    duplicateCheckInFlight.current = true
    const revision = scanRevision.current
    setCheckingDuplicates(true)
    setDuplicateCheckError(null)
    try {
      const result = await scanForDirectory()
      // A newer explicit scan owns the visible result.
      if (revision !== scanRevision.current) return
      const items = result.duplicate_files || []
      setDuplicateFiles(items)
      setSelectedDuplicatePath(previous =>
        items.some(item => item.path === previous) ? previous : items[0]?.path ?? null,
      )
      if (items.length === 0) setDuplicateModalOpen(false)
      setScanSummary(previous => previous ? {
        ...previous,
        new_found: previous.new_found + result.new_found,
        total: result.total,
        unprocessed: result.unprocessed,
      } : result)
    } catch (reason) {
      if (revision === scanRevision.current) {
        setDuplicateCheckError(tr("重新检查失败，当前显示上次扫描结果，请稍后重试。"))
      }
      console.warn('duplicate files refresh failed', reason)
    } finally {
      duplicateCheckInFlight.current = false
      setCheckingDuplicates(false)
    }
  }, [scanForDirectory])

  useEffect(() => {
    if (duplicateFiles.length === 0) return
    const refreshOnReturn = () => {
      if (document.visibilityState === 'visible') void refreshDuplicateFiles()
    }
    window.addEventListener('focus', refreshOnReturn)
    document.addEventListener('visibilitychange', refreshOnReturn)
    return () => {
      window.removeEventListener('focus', refreshOnReturn)
      document.removeEventListener('visibilitychange', refreshOnReturn)
    }
  }, [duplicateFiles.length, refreshDuplicateFiles])

  useEffect(() => () => { scanRevision.current += 1 }, [])

  const duplicateModal = duplicateModalOpen && duplicateFiles.length > 0 ? (
    <DuplicateFilesModal
      items={duplicateFiles}
      selectedPath={selectedDuplicatePath}
      onSelect={setSelectedDuplicatePath}
      onReveal={revealScannedFile}
      onRefresh={refreshDuplicateFiles}
      refreshing={checkingDuplicates}
      refreshError={duplicateCheckError}
      onClose={() => setDuplicateModalOpen(false)}
    />
  ) : null

  const runAll = async () => {
    if (
      runAllStatus.running ||
      busyKey ||
      state.processing?.running ||
      state.compileStatus?.running ||
      state.promotionRunStatus?.running
    ) return
    setError(null)
    setRunAllStep(tr("扫描论文目录"))
    try {
      const scanResult = await state.scan()
      showScanResult(scanResult)

      const pendingPapers = scanResult.pending ?? scanResult.unprocessed
      if (pendingPapers > 0) {
        setRunAllStep(tr("处理论文"), tr("{0} 篇待处理", { 0: pendingPapers }))
        await state.process()
        const processingResult = await waitForProcessingDone(s => {
          if (s.running) setRunAllStep(tr("处理论文"), `${s.done}/${s.total}`)
        })
        if (processingResult.errors > 0) {
          const firstFailure = processingResult.failedPapers[0]
          throw new Error(
            firstFailure?.reason ||
              processingResult.batchError ||
              tr("{0} 篇论文处理失败", { 0: processingResult.errors }),
          )
        }
      }

      const afterProcessing = await getStatus()
      if ((afterProcessing.failed_count ?? 0) > 0) {
        throw new Error(tr("有 {0} 篇失败论文，请先在录入阶段点击“重试失败”。", { 0: afterProcessing.failed_count }))
      }
      setRunAllStep(tr("自动筛选候选概念"))
      const beforePromotion = await getPromotionCounts()
      if ((beforePromotion.summary.counts.pending ?? 0) > 0) {
        await state.runPromotionRun(
          { use_llm: true, force_all: false },
          status => {
            if (status.phase === 'heuristic') {
              setRunAllStep(tr("自动筛选候选概念"), tr("启发式预筛选"))
            } else if (status.phase === 'llm') {
              setRunAllStep(
                tr("自动筛选候选概念"),
                status.total > 0
                  ? tr("Agent 判断 {0}/{1}", { 0: status.done, 1: status.total })
                  : tr("准备 Agent 判断"),
              )
            } else if (status.phase === 'reconcile') {
              setRunAllStep(tr("自动筛选候选概念"), tr("同步概念页与搜索索引"))
            }
          },
        )
      }
      const afterPromotion = await getPromotionCounts()
      if ((afterPromotion.summary.by.llm ?? 0) > 0) {
        setRunAllStep(tr("确认 Agent 筛选结果"), tr("{0} 个判断", { 0: afterPromotion.summary.by.llm }))
        await state.acceptPromotion()
      }

      let freshness = await getWikiFreshness()
      const paperIssues = freshness.papers.missing_count + freshness.papers.stale_count
      if (paperIssues > 0) {
        setRunAllStep(tr("编译论文页"), tr("{0} 个待处理", { 0: paperIssues }))
        await state.recompilePapers()
        await waitForWikiCompileDone(s => {
          if (s.running) setRunAllStep(tr("编译论文页"), `${s.done}/${s.total}`)
        })
      }

      freshness = await getWikiFreshness()
      const conceptIssues = freshness.concepts.missing_count + freshness.concepts.stale_count
      if (conceptIssues > 0) {
        setRunAllStep(tr("编译概念页"), tr("{0} 个待处理", { 0: conceptIssues }))
        await state.recompileConcepts()
        await waitForWikiCompileDone(s => {
          if (s.running) setRunAllStep(tr("编译概念页"), `${s.done}/${s.total}`)
        })
      }

      freshness = await getWikiFreshness()
      const compileTotalNodes =
        (freshness.papers.total_processed ?? 0) +
        (freshness.concepts.total_nodes ?? 0)
      if (compileTotalNodes > 0) {
        setRunAllStep(tr("运行健康检查"), tr("规则 + Agent"))
        await waitForWikiLint(await runWikiLint(true))
      }

      if (auth.configured && auth.user) {
        setRunAllStep(tr("同步到云端"), tr("准备快照"))
        const snapshot = await gatherLocalSnapshot({ since: getLastSyncAt() })
        await runSync(snapshot, progress => {
          if (progress.stage === 'uploading' && progress.uploadsTotal > 0) {
            setRunAllStep(tr("同步到云端"), tr("上传 {0}/{1}", { 0: progress.uploadsDone, 1: progress.uploadsTotal }))
          } else if (progress.stage !== 'idle') {
            setRunAllStep(tr("同步到云端"), syncStageLabel(progress.stage))
          }
        })
        setRunAllStatus({
          running: false,
          label: tr("流水线已全部完成"),
          detail: tr("已同步到云端"),
          tone: 'success',
        })
      } else {
        setRunAllStatus({
          running: false,
          label: tr("本地流水线已完成"),
          detail: tr("同步已跳过：请先登录云端账号"),
          tone: 'warning',
        })
      }
      state.refresh()
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      setError(message)
      setRunAllStatus({
        running: false,
        label: tr("全流程编排中断"),
        detail: message,
        tone: 'warning',
      })
    }
  }

  const safeRun = async (key: string, fn: () => Promise<unknown>) => {
    if (busyKey) return // already running something — ignore re-clicks
    setError(null)
    setBusyKey(key)
    try {
      await fn()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyKey(null)
    }
  }

  // Scan reports its result inline (new vs. skipped-duplicate counts).
  const scanNotice = scanSummary
    ? tr("扫描完成：新增 {0} 篇", { 0: scanSummary.new_found }) +
      (duplicateFiles.length > 0 ? tr(" · 当前重复文件 {0} 个（已跳过，不参与处理）", { 0: duplicateFiles.length })
        : scanSummary.duplicates > 0 ? tr(" · 重复文件已清理") : '') +
      tr(" · 待处理 {0} 篇", { 0: state.processing?.pending ?? scanSummary.pending ?? scanSummary.unprocessed }) +
      ((state.processing?.failedCount ?? scanSummary.failed_count ?? 0) > 0
        ? tr(" · 失败待重试 {0} 篇", { 0: state.processing?.failedCount ?? scanSummary.failed_count }) : '') +
      (duplicateCheckError ? ` · ${duplicateCheckError}` : '')
    : null
  const runScan = async () => {
    if (busyKey) return
    setError(null)
    scanRevision.current += 1
    setScanSummary(null)
    setDuplicateFiles([])
    setDuplicateModalOpen(false)
    setBusyKey('scan')
    try {
      const r = await state.scan()
      showScanResult(r)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyKey(null)
    }
  }

  if (collapsed) {
    return (
      <>
        <aside className="shrink-0 w-12 border-r border-slate-800/80 bg-[var(--surface-0d1016)] flex flex-col items-center py-3 gap-3">
          <button
            onClick={() => setCollapsed(false)}
            className="p-1.5 text-slate-400 hover:text-foreground rounded-md hover:bg-slate-800/60"
            title={tr("展开流水线控制台")}
          >
            <PanelLeftOpen size={16} />
          </button>
          <div className="w-full border-t border-slate-800/80" />
          {state.stages.map(s => (
            <button
              key={s.id}
              onClick={() => openFromIconRail(s.id)}
              className={`relative p-1.5 rounded-md hover:bg-slate-800/60 ${
                s.isNext ? 'ring-1 ring-indigo-400/50 bg-indigo-500/10' : ''
              }`}
              title={`${s.index} ${s.label} — ${s.headline}`}
            >
              <StageIcon stage={s.id} tone={s.tone} />
              {s.isNext && (
                <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-indigo-400 animate-pulse" />
              )}
            </button>
          ))}
          <button
            onClick={() => openFromIconRail('sync')}
            className="relative p-1.5 rounded-md hover:bg-slate-800/60 text-slate-400 hover:text-slate-100"
            title={tr("⑤ 同步 — 推送到云端")}
          >
            <CloudUpload size={14} />
          </button>
          <div className="mt-auto" />
          <button
            onClick={onOpenAsk}
            className="p-1.5 text-indigo-300 hover:text-foreground rounded-md hover:bg-indigo-500/20"
            title={tr("向知识库提问")}
          >
            <Sparkles size={16} />
          </button>
        </aside>
        {duplicateModal}
      </>
    )
  }

  return (
    <aside className="shrink-0 w-[19rem] border-r border-slate-800/80 bg-[var(--surface-0d1016)] flex flex-col">
      {/* Header */}
      <header className="flex items-center gap-2 px-4 py-2.5 border-b border-slate-800/60">
        <Sparkles size={12} className="text-indigo-300" />
        <span className="text-[11px] tracking-wider uppercase text-slate-400 font-semibold">
          {tr("流水线控制台")}</span>
        <button
          onClick={() => setCollapsed(true)}
          className="ml-auto p-1 rounded-md text-slate-500 hover:text-slate-200 hover:bg-slate-800/60"
          title={tr("折叠为图标条")}
        >
          <PanelLeftClose size={14} />
        </button>
      </header>

      <div className="border-b border-slate-800/60 px-3 py-2">
        <button
          onClick={runAll}
          disabled={runAllStatus.running || busyKey !== null || !!state.processing?.running || !!state.compileStatus?.running}
          className={`w-full inline-flex items-center justify-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${runAllButtonClass(runAllStatus.tone)}`}
          title={tr("按顺序执行：扫描、处理、筛选、编译、健检，并在已登录时同步到云端")}
        >
          {runAllStatus.running ? (
            <Loader2 size={13} className="animate-spin" />
          ) : runAllStatus.tone === 'success' ? (
            <CheckCircle2 size={13} />
          ) : (
            <Sparkles size={13} />
          )}
          {runAllStatus.running ? runAllStatus.label : tr("全流程编排")}
        </button>
        {(runAllStatus.detail || runAllStatus.tone !== 'idle') && (
          <p className={`mt-1 text-[11px] leading-relaxed ${runAllTextClass(runAllStatus.tone)}`}>
            {runAllStatus.detail || runAllStatus.label}
          </p>
        )}
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-2">
        {state.stages.map(stage => (
          <StageCard
            key={stage.id}
            stage={stage}
            expanded={expandedStages.has(stage.id)}
            onToggle={() => toggleStage(stage.id)}
          >
            {stage.id === 'ingest' && (
              <IngestActions
                state={state}
                busyKey={busyKey}
                scanNotice={scanNotice}
                onScan={runScan}
                onProcess={() => safeRun('process', state.process)}
                onRetry={() => safeRun('retry', state.retryFailed)}
              />
            )}
            {stage.id === 'curate' && (
              <CurateActions
                state={state}
                busyKey={busyKey}
                useLlm={useLlm}
                setUseLlm={setUseLlm}
                forceAll={forceAll}
                setForceAll={setForceAll}
                candidateMode={candidateMode}
                onCandidateModeChange={onCandidateModeChange}
                onOpenRescue={onOpenRescue}
                onOpenPromptEditor={() => setPromptEditorOpen(true)}
                onRun={() =>
                  safeRun('curate-run', () =>
                    state.runPromotionRun({ use_llm: useLlm, force_all: forceAll }),
                  )
                }
                onAccept={() => safeRun('curate-accept', state.acceptPromotion)}
              />
            )}
            {stage.id === 'compile' && (
              <CompileActions
                state={state}
                busyKey={busyKey}
                onCompilePapers={() => safeRun('compile-papers', state.recompilePapers)}
                onCompileConcepts={() => safeRun('compile-concepts', state.recompileConcepts)}
              />
            )}
            {stage.id === 'maintain' && (
              <MaintainActions state={state} onOpenLint={onOpenLint} />
            )}
          </StageCard>
        ))}

        {/* ⑤ Sync — kept outside the StageId state machine; see
            SyncStageCard for the rationale. */}
        <SyncStageCard
          expanded={expandedStages.has('sync')}
          onToggle={() => toggleStage('sync')}
        />

        {error && (
          <div className="px-3 py-2 rounded-lg border border-rose-500/40 bg-rose-500/10 text-[11.5px] text-rose-200">
            {error}
          </div>
        )}
      </div>

      {/* Ask button — distinct read action, anchored at bottom */}
      <div className="px-3 py-3 border-t border-slate-800/60">
        <button
          onClick={onOpenAsk}
          className="w-full inline-flex items-center justify-center gap-1.5 text-[13px] font-medium text-white bg-gradient-to-br from-indigo-500 to-violet-500 hover:from-indigo-400 hover:to-violet-400 px-3 py-2 rounded-xl transition-colors shadow-lg shadow-indigo-500/20"
        >
          <Sparkles size={14} />
          {tr("Ask · 跨论文提问")}</button>
      </div>

      <PromotionPromptEditor
        open={promptEditorOpen}
        onClose={() => setPromptEditorOpen(false)}
      />
      {duplicateModal}
    </aside>
  )
}

// --- Stage card shell -------------------------------------------------

function StageCard({
  stage,
  expanded,
  onToggle,
  children,
}: {
  stage: StageSnapshot
  expanded: boolean
  onToggle: () => void
  children: ReactNode
}) {
  useLocale()
  const palette = stagePalette(stage.tone)
  return (
    <section
      className={`rounded-xl border transition-all ${palette.border} ${palette.bg} ${
        stage.isNext
          ? 'ring-1 ring-indigo-400/60 shadow-md shadow-indigo-500/10'
          : ''
      }`}
    >
      <button
        onClick={onToggle}
        aria-expanded={expanded}
        title={expanded ? tr("收起{0}", { 0: stage.label }) : tr("展开{0}", { 0: stage.label })}
        className="flex w-full min-w-0 flex-col gap-1.5 px-3 py-2.5 text-left"
      >
        <span className="flex w-full min-w-0 items-start gap-2 leading-5">
          {expanded ? (
            <ChevronDown size={13} className="mt-1 shrink-0 text-slate-500" />
          ) : (
            <ChevronRight size={13} className="mt-1 shrink-0 text-slate-600" />
          )}
          <span className={`shrink-0 text-[12px] font-mono tabular-nums ${palette.indexColor}`}>
            {stage.index}
          </span>
          <span className="mt-0.5 shrink-0">
            <StageIcon stage={stage.id} tone={stage.tone} />
          </span>
          <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
            <span className="min-w-0 break-words text-[13px] font-semibold text-slate-100">
              {stage.label}
            </span>
            {stage.isNext && (
              <span className="max-w-full break-words rounded-full border border-indigo-400/40 bg-indigo-500/20 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-indigo-200">
                {tr("建议")}</span>
            )}
          </span>
        </span>
        <span
          className="w-full min-w-0 whitespace-normal break-words pl-[21px] text-[11.5px] leading-relaxed tabular-nums text-slate-300"
          title={stage.headline}
        >
          {stage.headline}
        </span>
      </button>
      {expanded && (
        <div className="px-3 pb-3 pt-2 border-t border-slate-800/40">
          {stage.sub && (
            <div className="mb-2 text-[11px] text-slate-400 leading-relaxed">
              {stage.sub}
            </div>
          )}
          {children}
        </div>
      )}
    </section>
  )
}

// --- Stage ① 录入 ----------------------------------------------------

function IngestActions({
  state,
  busyKey,
  scanNotice,
  onScan,
  onProcess,
  onRetry,
}: {
  state: PipelineState & PipelineActions
  busyKey: string | null
  scanNotice: string | null
  onScan: () => void
  onProcess: () => void
  onRetry: () => void
}) {
  useLocale()
  const running = !!state.processing?.running
  const anyBusy = busyKey !== null
  const submitting = busyKey === 'process' || busyKey === 'retry'
  const remaining = running
    ? Math.max(0, (state.processing?.total ?? 0) - (state.processing?.done ?? 0))
    : state.unprocessedHint
  const firstFailure = state.processing?.failedPapers[0]
  const failedCount = state.processing?.failedCount ?? 0
  const retryOnly = !running && remaining === 0 && failedCount > 0
  const failureReason = firstFailure?.reason || state.processing?.batchError
  return (
    <div className="space-y-2">
      <p className="text-[11.5px] text-slate-400 leading-relaxed">
        {tr("扫描本地 PDF 目录，把新论文喂给 LLM 抽取，并落入数据库。")}</p>
      <div className="flex flex-wrap gap-2">
        <ActionButton
          onClick={onScan}
          className="flex-[1_0_max-content]"
          icon={<ScanLine size={12} />}
          variant="ghost"
          disabled={running || anyBusy}
          loading={busyKey === 'scan'}
          loadingLabel={tr("扫描中")}
          title={tr("扫描 data/papers 目录，找出未入库的 PDF")}
        >
          {tr("扫描目录")}</ActionButton>
        <ActionButton
          onClick={retryOnly ? onRetry : onProcess}
          className="flex-[1_0_max-content]"
          icon={running || submitting ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
          variant="primary"
          disabled={running || anyBusy || (remaining === 0 && failedCount === 0)}
          loading={submitting}
          loadingLabel={tr("提交中")}
          title={state.processing?.current || (retryOnly ? tr("重新处理失败论文") : remaining > 0 ? tr("处理新的待处理论文") : tr("暂无待处理论文"))}
        >
          {running ? tr("处理中") : retryOnly ? tr("重试失败 {0} 篇", { 0: failedCount }) : remaining > 0 ? tr("处理 {0} 篇", { 0: remaining }) : tr("处理论文")}
        </ActionButton>
      </div>
      {scanNotice && !running && (
        <p className="rounded-md border border-emerald-500/25 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-200">
          {scanNotice}
        </p>
      )}
      {!running && remaining > 0 && failedCount > 0 && (
        <ActionButton onClick={onRetry} icon={<Play size={12} />} variant="ghost" disabled={anyBusy}>
          {tr("重试失败")}{' '}{failedCount} {tr("篇")}</ActionButton>
      )}
      {!running && (state.processing?.errors ?? 0) > 0 && (
        <div className="rounded-md border border-rose-500/35 bg-rose-500/10 px-2 py-1.5 text-[11px] leading-relaxed text-rose-200">
          <div className="flex items-start gap-1.5">
            <AlertTriangle size={12} className="mt-0.5 shrink-0" />
            <span>
              {state.processing?.lastMessage || tr("有 {0} 篇处理失败", { 0: state.processing?.errors })}
              {firstFailure?.filename ? tr("；首篇：{0}", { 0: firstFailure.filename }) : ''}
              {failureReason ? `；${failureReason}` : ''}
            </span>
          </div>
        </div>
      )}
      {running && (
        <ProgressLine
          done={state.processing?.done ?? 0}
          total={state.processing?.total ?? 0}
          errors={state.processing?.errors ?? 0}
          label={state.processing?.current}
        />
      )}
    </div>
  )
}

// --- Stage ② 筛选 ----------------------------------------------------

function CurateActions({
  state,
  busyKey,
  useLlm,
  setUseLlm,
  forceAll,
  setForceAll,
  candidateMode,
  onCandidateModeChange,
  onOpenRescue,
  onOpenPromptEditor,
  onRun,
  onAccept,
}: {
  state: PipelineState & PipelineActions
  busyKey: string | null
  useLlm: boolean
  setUseLlm: (v: boolean) => void
  forceAll: boolean
  setForceAll: (v: boolean) => void
  candidateMode: CandidateMode
  onCandidateModeChange: (m: CandidateMode) => void
  onOpenRescue: () => void
  onOpenPromptEditor: () => void
  onRun: () => void
  onAccept: () => void
}) {
  useLocale()
  const pending = state.promotion?.counts.pending ?? 0
  const llmDecided = state.promotion?.by.llm ?? 0
  const rejected = state.promotion?.counts.rejected ?? 0
  const userPinned = state.promotion?.by.user ?? 0
  const promptEmpty = state.promotionPromptConfigured === false
  const promotionRunning = !!state.promotionRunStatus?.running
  const anyBusy = busyKey !== null || promotionRunning

  return (
    <div className="space-y-2.5">
      <p className="text-[11.5px] text-slate-400 leading-relaxed">
        {tr("启发式 + Agent 自动剔除无效候选；人工抽查后再「确认 Agent 剔除」锁定。")}</p>

      {/* Counts strip */}
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px]">
        <Stat label={tr("待评")} value={pending} tone="amber" />
        <Stat label={tr("已选")} value={state.promotion?.counts.promoted ?? 0} tone="emerald" />
        <Stat label={tr("淘汰")} value={rejected} tone="rose" />
        <span className="text-slate-700">·</span>
        <Stat label="human" value={userPinned} tone="slate" icon={<Hand size={9} />} />
        <Stat label="agent" value={llmDecided} tone="slate" icon={<Bot size={9} />} />
      </div>

      {state.promotionRunStatus?.phase === 'error' && (
        <p className="rounded-md border border-rose-500/30 bg-rose-500/10 px-2 py-1.5 text-[10.5px] text-rose-200 break-words">
          {state.promotionRunStatus.error || tr("概念筛选后台任务失败，请重试。")}
        </p>
      )}

      {/* Primary action: run promotion */}
      <div className="flex flex-wrap gap-2">
        <ActionButton
          onClick={onRun}
          className="flex-[1_0_max-content]"
          icon={<Zap size={12} />}
          variant="primary"
          disabled={anyBusy}
          loading={busyKey === 'curate-run' || promotionRunning}
          loadingLabel={
            promotionRunning && state.promotionRunStatus?.phase === 'llm' && state.promotionRunStatus.total > 0
              ? `${state.promotionRunStatus.done}/${state.promotionRunStatus.total}`
              : tr("剔除中")
          }
          title={tr("对所有候选节点跑启发式 + Agent 剔除")}
        >
          {tr("自动剔除")}</ActionButton>
        <ActionButton
          onClick={onAccept}
          className="flex-[1_0_max-content]"
          icon={<ShieldCheck size={12} />}
          variant="ghost"
          disabled={llmDecided === 0 || anyBusy}
          loading={busyKey === 'curate-accept'}
          loadingLabel={tr("确认中")}
          title={tr("把 Agent 的剔除结果锁成 human 确定")}
        >
          {tr("确认 Agent")}{' '}{llmDecided > 0 ? `(${llmDecided})` : ''}
        </ActionButton>
      </div>

      {/* Promotion settings */}
      <div className="flex flex-wrap gap-x-3 gap-y-1.5 text-[11px] text-slate-400">
        <label
          className="inline-flex items-center gap-1 cursor-pointer"
          title={
            promptEmpty
              ? tr("提示词未配置，即使勾上 Agent 也会被跳过")
              : tr("不勾时只跑启发式，速度快但灰色地带保留待修订")
          }
        >
          <input
            type="checkbox"
            checked={useLlm}
            onChange={e => setUseLlm(e.target.checked)}
            className="accent-indigo-500 h-3 w-3"
          />
          <span className={promptEmpty ? 'text-amber-300/90' : undefined}>
            {tr("调用 Agent")}{' '}{promptEmpty ? tr("（未配置）") : ''}
          </span>
        </label>
        <label className="inline-flex items-center gap-1 cursor-pointer" title={tr("忽略 30 天冷却期")}>
          <input
            type="checkbox"
            checked={forceAll}
            onChange={e => setForceAll(e.target.checked)}
            className="accent-indigo-500 h-3 w-3"
          />
          <span>{tr("强制重剔")}</span>
        </label>
        <button
          onClick={onOpenPromptEditor}
          className="inline-flex items-center gap-1 text-slate-400 hover:text-slate-200"
          title={tr("编辑发给 Agent 的剔除提示词")}
        >
          <Pencil size={10} /> {tr("提示词")}</button>
      </div>

      {/* Candidate visibility mode */}
      <div className="pt-1.5">
        <div className="text-[10.5px] uppercase tracking-wider text-slate-500 mb-1">
          {tr("图谱可见范围")}</div>
        <div className="grid grid-cols-3 gap-1 rounded-lg bg-slate-950/60 p-1 border border-slate-800">
          {(
            [
              { id: 'off', label: tr("仅选中"), icon: <EyeOff size={10} />, disabled: false },
              {
                id: 'pending',
                label: pending > 0 ? tr("候选({0})", { 0: pending }) : tr("候选"),
                icon: <Eye size={10} />,
                disabled: pending === 0,
              },
              {
                id: 'all',
                label: tr("全量"),
                icon: <Eye size={10} />,
                disabled: pending === 0 && rejected === 0,
              },
            ] as const
          ).map(opt => {
            const active = candidateMode === opt.id
            return (
              <button
                key={opt.id}
                onClick={() => onCandidateModeChange(opt.id)}
                disabled={opt.disabled && !active}
                className={`inline-flex items-center justify-center gap-1 text-[10.5px] py-1 px-1 rounded-md transition-colors ${
                  active
                    ? 'bg-slate-800 text-foreground shadow-inner'
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
      </div>

      <ActionButton
        onClick={onOpenRescue}
        icon={<Trash2 size={11} />}
        variant="ghost"
        disabled={rejected === 0}
        title={tr("打开回收站，召回误剔节点")}
        className="w-full"
      >
        {tr("召回误剔")}{' '}{rejected > 0 ? ` (${rejected})` : ''}
      </ActionButton>
    </div>
  )
}

// --- Stage ③ 编译 ----------------------------------------------------

function CompileActions({
  state,
  busyKey,
  onCompilePapers,
  onCompileConcepts,
}: {
  state: PipelineState & PipelineActions
  busyKey: string | null
  onCompilePapers: () => void
  onCompileConcepts: () => void
}) {
  useLocale()
  const running = !!state.compileStatus?.running
  const runningKind = state.compileStatus?.kind
  const anyBusy = busyKey !== null
  const f = state.freshness
  const paperIssues =
    (f?.papers.missing_count ?? 0) + (f?.papers.stale_count ?? 0)
  const conceptIssues =
    (f?.concepts.missing_count ?? 0) + (f?.concepts.stale_count ?? 0)
  return (
    <div className="space-y-2">
      <p className="text-[11.5px] text-slate-400 leading-relaxed">
        {tr("把 DB 里的论文与概念编译为可读的 wiki .md（被 Ask / 全文搜索使用）。")}</p>
      <div className="space-y-1.5">
        <CompileRow
          icon={<FileText size={12} />}
          label={tr("论文页")}
          ok={f?.papers.ok ?? 0}
          total={f?.papers.total_processed ?? 0}
          missing={f?.papers.missing_count ?? 0}
          stale={f?.papers.stale_count ?? 0}
          orphan={f?.papers.orphan_count ?? 0}
          runningHere={(running && runningKind === 'papers') || busyKey === 'compile-papers'}
          progress={state.compileStatus}
          onClick={onCompilePapers}
          disabled={running || anyBusy || paperIssues === 0}
        />
        <CompileRow
          icon={<BookMarked size={12} />}
          label={tr("概念页")}
          ok={f?.concepts.ok ?? 0}
          total={f?.concepts.total_nodes ?? 0}
          missing={f?.concepts.missing_count ?? 0}
          stale={f?.concepts.stale_count ?? 0}
          orphan={f?.concepts.orphan_count ?? 0}
          runningHere={(running && runningKind === 'concepts') || busyKey === 'compile-concepts'}
          progress={state.compileStatus}
          onClick={onCompileConcepts}
          disabled={running || anyBusy || conceptIssues === 0}
        />
      </div>
    </div>
  )
}

function CompileRow({
  icon,
  label,
  ok,
  total,
  missing,
  stale,
  orphan,
  runningHere,
  progress,
  onClick,
  disabled,
}: {
  icon: ReactNode
  label: string
  ok: number
  total: number
  missing: number
  stale: number
  orphan: number
  runningHere: boolean
  progress: PipelineState['compileStatus']
  onClick: () => void
  disabled: boolean
}) {
  useLocale()
  const issues = missing + stale + orphan
  // Three terminal states: running (compiling now) → pending (something
  // to compile) → done (total>0 and everything ok). total==0 means
  // there's nothing in the DB to compile yet.
  const allOk = total > 0 && issues === 0 && ok === total
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/60 px-2.5 py-1.5">
      <div className="flex items-center gap-2">
        <span className="text-slate-400">{icon}</span>
        <span className="text-[12px] text-slate-200 font-medium">{label}</span>
        <span className={`ml-auto text-[11px] tabular-nums ${allOk && !runningHere ? 'text-emerald-300' : 'text-slate-400'}`}>
          {total === 0 ? tr("尚未编译") : `${ok}/${total}`}
        </span>
      </div>
      {issues > 0 && !runningHere && (
        <div className="mt-0.5 text-[10.5px] text-slate-500 tabular-nums">
          {missing > 0 && tr("{0} 待编译 ", { 0: missing })}
          {stale > 0 && tr("{0} 已过期 ", { 0: stale })}
          {orphan > 0 && tr("{0} 孤儿", { 0: orphan })}
        </div>
      )}
      {runningHere ? (
        <button
          disabled
          className="mt-1.5 w-full inline-flex items-center justify-center gap-1.5 text-[11px] px-2 py-1 rounded-md border border-indigo-500/40 bg-indigo-500/10 text-indigo-200 cursor-not-allowed"
        >
          <Loader2 size={11} className="animate-spin" />
          {tr("编译中…")}</button>
      ) : allOk ? (
        // Done state: green "up to date" pill. Stays until the next
        // freshness poll surfaces new missing/stale items, which flips
        // this back to the amber 重编译 button.
        <div className="mt-1.5 w-full inline-flex items-center justify-center gap-1.5 text-[11px] px-2 py-1 rounded-md border border-emerald-500/40 bg-emerald-500/10 text-emerald-300">
          <CheckCircle2 size={11} />
          {tr("已是最新")}</div>
      ) : total > 0 ? (
        <button
          onClick={onClick}
          disabled={disabled}
          className="mt-1.5 w-full inline-flex items-center justify-center gap-1.5 text-[11px] px-2 py-1 rounded-md border border-amber-500/40 bg-amber-500/10 text-amber-200 hover:bg-amber-500/20 disabled:opacity-50 transition-colors"
        >
          <Sparkles size={11} />
          {missing === total ? tr("编译") : tr("重编译")}
        </button>
      ) : null}
      {runningHere && progress && progress.total > 0 && (
        <div className="mt-1 h-1 bg-slate-800/70 rounded">
          <div
            className="h-full bg-indigo-400 rounded transition-all duration-300"
            style={{ width: `${Math.max(4, Math.round((progress.done / progress.total) * 100))}%` }}
          />
        </div>
      )}
    </div>
  )
}

// --- Stage ④ 健检 ----------------------------------------------------

function MaintainActions({
  state,
  onOpenLint,
}: {
  state: PipelineState & PipelineActions
  onOpenLint: () => void
}) {
  useLocale()
  const lint = state.lintStatus
  const compileTotalNodes =
    (state.freshness?.papers.total_processed ?? 0) +
    (state.freshness?.concepts.total_nodes ?? 0)
  const empty = compileTotalNodes === 0
  return (
    <div className="space-y-2">
      <p className="text-[11.5px] text-slate-400 leading-relaxed">
        {tr("扫描 wiki .md，找出短桩 / 可合并 / 待建概念 / 追问建议，并给出可执行操作。")}</p>
      <div className="grid grid-cols-1 gap-2">
        <ActionButton
          onClick={onOpenLint}
          icon={<Stethoscope size={12} />}
          variant="primary"
          disabled={empty}
          title={empty ? tr("尚无可检的编译内容") : tr("运行健康检查（自动 + Agent）")}
        >
          {lint?.exists ? tr("查看 / 重跑健康检查") : tr("运行健康检查")}
        </ActionButton>
        {lint?.exists && (
          <button
            onClick={onOpenLint}
            className="inline-flex items-center justify-center gap-1.5 text-[11px] text-slate-400 hover:text-slate-200 px-2 py-1 rounded-md border border-slate-700/80 bg-slate-900/40"
            title={lint.rel_path}
          >
            <FileSearch size={11} /> {tr("查看最新报告")}</button>
        )}
      </div>
    </div>
  )
}

// --- shared subcomponents --------------------------------------------

function StageIcon({ stage, tone }: { stage: StageId; tone: StageSnapshot['tone'] }) {
  useLocale()
  const palette = stagePalette(tone)
  const base = (() => {
    switch (stage) {
      case 'ingest':
        return <ScanLine size={13} />
      case 'curate':
        return <Zap size={13} />
      case 'compile':
        return <BookMarked size={13} />
      case 'maintain':
        return <Stethoscope size={13} />
    }
  })()
  return <span className={palette.iconColor}>{base}</span>
}

function ActionButton({
  onClick,
  icon,
  variant,
  disabled,
  loading,
  loadingLabel,
  title,
  className,
  children,
}: {
  onClick: () => void
  icon: ReactNode
  variant: 'primary' | 'ghost'
  disabled?: boolean
  /** This button's own action is in-flight: swap icon → spinner and
   *  (optionally) the label, on top of being disabled. */
  loading?: boolean
  loadingLabel?: string
  title?: string
  className?: string
  children: ReactNode
}) {
  useLocale()
  const cls =
    variant === 'primary'
      ? 'bg-indigo-500 hover:bg-indigo-400 text-white border-indigo-400/60'
      : 'bg-slate-900/60 hover:bg-slate-800 text-slate-200 border-slate-700'
  return (
    <button
      onClick={onClick}
      disabled={disabled || loading}
      title={title}
      className={`inline-flex min-w-0 max-w-full items-center justify-center gap-1.5 whitespace-nowrap text-[11.5px] font-medium px-2.5 py-1.5 rounded-lg border transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${cls} ${className || ''}`}
    >
      <span className="inline-flex shrink-0">
        {loading ? <Loader2 size={12} className="animate-spin" /> : icon}
      </span>
      <span className="min-w-0 truncate">{loading && loadingLabel ? loadingLabel : children}</span>
    </button>
  )
}

function Stat({
  label,
  value,
  tone,
  icon,
}: {
  label: string
  value: number
  tone: 'amber' | 'emerald' | 'rose' | 'slate'
  icon?: ReactNode
}) {
  useLocale()
  const palette = {
    amber: 'text-amber-300',
    emerald: 'text-emerald-300',
    rose: 'text-rose-300',
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

function ProgressLine({
  done,
  total,
  errors,
  label,
}: {
  done: number
  total: number
  errors: number
  label?: string
}) {
  useLocale()
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0
  return (
    <div>
      <div className="h-1 bg-slate-800/70 rounded">
        <div
          className="h-full bg-indigo-400 rounded transition-all duration-300"
          style={{ width: `${Math.max(4, pct)}%` }}
        />
      </div>
      <div className="mt-1 text-[10.5px] tabular-nums text-indigo-200/80 flex justify-between">
        <span className="truncate max-w-[14rem]" title={label}>{label || ' '}</span>
        <span>
          {done}/{total}
          {errors > 0 ? tr(" · {0} 失败", { 0: errors }) : ''}
        </span>
      </div>
    </div>
  )
}

function stagePalette(tone: StageSnapshot['tone']) {
  switch (tone) {
    case 'ok':
      return {
        border: 'border-emerald-500/25',
        bg: 'bg-emerald-500/[0.04]',
        iconColor: 'text-emerald-300',
        indexColor: 'text-emerald-300/80',
      }
    case 'warning':
      return {
        border: 'border-amber-500/30',
        bg: 'bg-amber-500/[0.05]',
        iconColor: 'text-amber-300',
        indexColor: 'text-amber-300/80',
      }
    case 'danger':
      return {
        border: 'border-rose-500/40',
        bg: 'bg-rose-500/[0.05]',
        iconColor: 'text-rose-300',
        indexColor: 'text-rose-300/80',
      }
    case 'running':
      return {
        border: 'border-indigo-500/40',
        bg: 'bg-indigo-500/[0.06]',
        iconColor: 'text-indigo-300',
        indexColor: 'text-indigo-300/80',
      }
    case 'idle':
    default:
      return {
        border: 'border-slate-800',
        bg: 'bg-slate-900/40',
        iconColor: 'text-slate-500',
        indexColor: 'text-slate-500',
      }
  }
}

type ProcessingPollStatus = NonNullable<PipelineState['processing']>

const sleep = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms))

async function waitForProcessingDone(onTick: (status: ProcessingPollStatus) => void) {
  let sawRunning = false
  let idlePolls = 0
  for (;;) {
    const raw = await getStatus()
    const status: ProcessingPollStatus = {
      running: !!raw.running,
      total: raw.total ?? 0,
      done: raw.done ?? 0,
      errors: raw.errors ?? 0,
      current: raw.current ?? '',
      succeeded: raw.succeeded ?? Math.max(0, (raw.done ?? 0) - (raw.errors ?? 0)),
      pending: typeof raw.pending === 'number' ? raw.pending : null,
      failedCount: raw.failed_count ?? 0,
      failedPapers: (raw.failed_papers ?? []).map(item => ({
        id: item.id,
        filename: item.filename,
        stage: item.stage,
        reason: item.reason,
        recoverable: item.recoverable,
        retryCount: item.retry_count,
      })),
      batchError: raw.batch_error ?? null,
      lastMessage: raw.last_message ?? raw.message ?? '',
    }
    onTick(status)
    if (status.running) {
      sawRunning = true
      idlePolls = 0
    } else {
      if (sawRunning || idlePolls >= 2) return status
      idlePolls += 1
    }
    await sleep(1500)
  }
}

async function waitForWikiCompileDone(onTick: (status: WikiCompileState) => void) {
  let sawRunning = false
  let idlePolls = 0
  for (;;) {
    const status = await getWikiStatus()
    onTick(status)
    if (status.running) {
      sawRunning = true
      idlePolls = 0
    } else {
      if (sawRunning || idlePolls >= 2) return status
      idlePolls += 1
    }
    await sleep(1500)
  }
}

function syncStageLabel(stage: string): string {
  switch (stage) {
    case 'preparing':
      return tr("准备中")
    case 'uploading':
      return tr("上传中")
    case 'committing':
      return tr("提交中")
    case 'done':
      return tr("提交完成")
    case 'error':
      return tr("同步失败")
    default:
      return tr("同步中")
  }
}

function runAllButtonClass(tone: RunAllTone): string {
  if (tone === 'running') return 'border-indigo-400/60 bg-indigo-500/20 text-indigo-100'
  if (tone === 'success') return 'border-emerald-400/50 bg-emerald-500/15 text-emerald-200'
  if (tone === 'warning') return 'border-amber-400/50 bg-amber-500/15 text-amber-200'
  return 'border-indigo-500/40 bg-indigo-500/15 text-indigo-100 hover:bg-indigo-500/25'
}

function runAllTextClass(tone: RunAllTone): string {
  if (tone === 'success') return 'text-emerald-300/90'
  if (tone === 'warning') return 'text-amber-200/90'
  if (tone === 'running') return 'text-indigo-200/90'
  return 'text-slate-500'
}

// Type re-exports for the rare case a consumer needs them. Most consumers
// should just hand a `state` prop returned by usePipelineState().
export type { StageId, StageSnapshot, CandidateMode }
// Re-export shared icons so consumers can build icon-only previews without
// importing both lucide-react and this file directly.
export { CheckCircle2, AlertTriangle, Loader2 }
