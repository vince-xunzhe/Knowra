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

import { useEffect, useRef, useState, type ReactNode } from 'react'
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
  runWikiLint,
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
  const auth = useCloudAuth()
  const [collapsed, setCollapsed] = useState(false)
  const [expandedStages, setExpandedStages] = useState<Set<ConsoleStageId>>(() => new Set())
  const [error, setError] = useState<string | null>(null)
  const [promptEditorOpen, setPromptEditorOpen] = useState(false)
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
    label: '全流程编排',
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

  const runAll = async () => {
    if (runAllStatus.running || busyKey || state.processing?.running || state.compileStatus?.running) return
    setError(null)
    setRunAllStep('扫描论文目录')
    try {
      const scanResult = await state.scan()

      if (scanResult.unprocessed > 0) {
        setRunAllStep('处理论文', `${scanResult.unprocessed} 篇待处理`)
        await state.process()
        await waitForProcessingDone(s => {
          if (s.running) setRunAllStep('处理论文', `${s.done}/${s.total}`)
        })
      }

      setRunAllStep('自动筛选候选概念')
      const beforePromotion = await getPromotionCounts()
      if ((beforePromotion.summary.counts.pending ?? 0) > 0) {
        await state.runPromotionRun({ use_llm: true, force_all: false })
      }
      const afterPromotion = await getPromotionCounts()
      if ((afterPromotion.summary.by.llm ?? 0) > 0) {
        setRunAllStep('确认 Agent 筛选结果', `${afterPromotion.summary.by.llm} 个判断`)
        await state.acceptPromotion()
      }

      let freshness = await getWikiFreshness()
      const paperIssues = freshness.papers.missing_count + freshness.papers.stale_count
      if (paperIssues > 0) {
        setRunAllStep('编译论文页', `${paperIssues} 个待处理`)
        await state.recompilePapers()
        await waitForWikiCompileDone(s => {
          if (s.running) setRunAllStep('编译论文页', `${s.done}/${s.total}`)
        })
      }

      freshness = await getWikiFreshness()
      const conceptIssues = freshness.concepts.missing_count + freshness.concepts.stale_count
      if (conceptIssues > 0) {
        setRunAllStep('编译概念页', `${conceptIssues} 个待处理`)
        await state.recompileConcepts()
        await waitForWikiCompileDone(s => {
          if (s.running) setRunAllStep('编译概念页', `${s.done}/${s.total}`)
        })
      }

      freshness = await getWikiFreshness()
      const compileTotalNodes =
        (freshness.papers.total_processed ?? 0) +
        (freshness.concepts.total_nodes ?? 0)
      if (compileTotalNodes > 0) {
        setRunAllStep('运行健康检查', '规则 + Agent')
        await runWikiLint(true)
      }

      if (auth.configured && auth.user) {
        setRunAllStep('同步到云端', '准备快照')
        const snapshot = await gatherLocalSnapshot({ since: getLastSyncAt() })
        await runSync(snapshot, progress => {
          if (progress.stage === 'uploading' && progress.uploadsTotal > 0) {
            setRunAllStep('同步到云端', `上传 ${progress.uploadsDone}/${progress.uploadsTotal}`)
          } else if (progress.stage !== 'idle') {
            setRunAllStep('同步到云端', syncStageLabel(progress.stage))
          }
        })
        setRunAllStatus({
          running: false,
          label: '流水线已全部完成',
          detail: '已同步到云端',
          tone: 'success',
        })
      } else {
        setRunAllStatus({
          running: false,
          label: '本地流水线已完成',
          detail: '同步已跳过：请先登录云端账号',
          tone: 'warning',
        })
      }
      state.refresh()
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      setError(message)
      setRunAllStatus({
        running: false,
        label: '全流程编排中断',
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
  const [scanNotice, setScanNotice] = useState<string | null>(null)
  const runScan = async () => {
    if (busyKey) return
    setError(null)
    setScanNotice(null)
    setBusyKey('scan')
    try {
      const r = await state.scan()
      setScanNotice(
        `扫描完成：新增 ${r.new_found} 篇` +
          (r.duplicates > 0 ? ` · 跳过重复 ${r.duplicates} 篇` : '') +
          ` · 待处理 ${r.unprocessed} 篇`,
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusyKey(null)
    }
  }

  if (collapsed) {
    return (
      <aside className="shrink-0 w-12 border-r border-slate-800/80 bg-[#0d1016] flex flex-col items-center py-3 gap-3">
        <button
          onClick={() => setCollapsed(false)}
          className="p-1.5 text-slate-400 hover:text-white rounded-md hover:bg-slate-800/60"
          title="展开流水线控制台"
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
          title="⑤ 同步 — 推送到云端"
        >
          <CloudUpload size={14} />
        </button>
        <div className="mt-auto" />
        <button
          onClick={onOpenAsk}
          className="p-1.5 text-indigo-300 hover:text-white rounded-md hover:bg-indigo-500/20"
          title="向知识库提问"
        >
          <Sparkles size={16} />
        </button>
      </aside>
    )
  }

  return (
    <aside className="shrink-0 w-[19rem] border-r border-slate-800/80 bg-[#0d1016] flex flex-col">
      {/* Header */}
      <header className="flex items-center gap-2 px-4 py-2.5 border-b border-slate-800/60">
        <Sparkles size={12} className="text-indigo-300" />
        <span className="text-[11px] tracking-wider uppercase text-slate-400 font-semibold">
          流水线控制台
        </span>
        <button
          onClick={() => setCollapsed(true)}
          className="ml-auto p-1 rounded-md text-slate-500 hover:text-slate-200 hover:bg-slate-800/60"
          title="折叠为图标条"
        >
          <PanelLeftClose size={14} />
        </button>
      </header>

      <div className="border-b border-slate-800/60 px-3 py-2">
        <button
          onClick={runAll}
          disabled={runAllStatus.running || busyKey !== null || !!state.processing?.running || !!state.compileStatus?.running}
          className={`w-full inline-flex items-center justify-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${runAllButtonClass(runAllStatus.tone)}`}
          title="按顺序执行：扫描、处理、筛选、编译、健检，并在已登录时同步到云端"
        >
          {runAllStatus.running ? (
            <Loader2 size={13} className="animate-spin" />
          ) : runAllStatus.tone === 'success' ? (
            <CheckCircle2 size={13} />
          ) : (
            <Sparkles size={13} />
          )}
          {runAllStatus.running ? runAllStatus.label : '全流程编排'}
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
          Ask · 跨论文提问
        </button>
      </div>

      <PromotionPromptEditor
        open={promptEditorOpen}
        onClose={() => setPromptEditorOpen(false)}
      />
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
        title={expanded ? `收起${stage.label}` : `展开${stage.label}`}
        className="grid w-full grid-cols-[auto_minmax(0,1fr)] items-center gap-2 px-3 py-2.5 text-left"
      >
        <span className="flex min-w-0 items-center gap-2">
          {expanded ? (
            <ChevronDown size={13} className="shrink-0 text-slate-500" />
          ) : (
            <ChevronRight size={13} className="shrink-0 text-slate-600" />
          )}
          <span className={`shrink-0 text-[12px] font-mono tabular-nums ${palette.indexColor}`}>
            {stage.index}
          </span>
          <span className="shrink-0">
            <StageIcon stage={stage.id} tone={stage.tone} />
          </span>
          <span className="shrink-0 whitespace-nowrap text-[13px] font-semibold text-slate-100">
            {stage.label}
          </span>
          {stage.isNext && (
            <span className="shrink-0 rounded-full border border-indigo-400/40 bg-indigo-500/20 px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-indigo-200">
              建议
            </span>
          )}
        </span>
        <span
          className="min-w-0 justify-self-end truncate whitespace-nowrap text-right text-[11.5px] tabular-nums text-slate-300"
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
}: {
  state: PipelineState & PipelineActions
  busyKey: string | null
  scanNotice: string | null
  onScan: () => void
  onProcess: () => void
}) {
  const running = !!state.processing?.running
  const anyBusy = busyKey !== null
  const remaining = running
    ? Math.max(0, (state.processing?.total ?? 0) - (state.processing?.done ?? 0))
    : state.unprocessedHint
  return (
    <div className="space-y-2">
      <p className="text-[11.5px] text-slate-400 leading-relaxed">
        扫描本地 PDF 目录，把新论文喂给 LLM 抽取，并落入数据库。
      </p>
      <div className="grid grid-cols-2 gap-2">
        <ActionButton
          onClick={onScan}
          icon={<ScanLine size={12} />}
          variant="ghost"
          disabled={running || anyBusy}
          loading={busyKey === 'scan'}
          loadingLabel="扫描中"
          title="扫描 data/papers 目录，找出未入库的 PDF"
        >
          扫描目录
        </ActionButton>
        <ActionButton
          onClick={onProcess}
          icon={running ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
          variant="primary"
          disabled={running || anyBusy}
          title={state.processing?.current || '处理所有未入库论文'}
        >
          {running ? '处理中' : remaining > 0 ? `处理 ${remaining} 篇` : '处理论文'}
        </ActionButton>
      </div>
      {scanNotice && !running && (
        <p className="rounded-md border border-emerald-500/25 bg-emerald-500/10 px-2 py-1 text-[11px] text-emerald-200">
          {scanNotice}
        </p>
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
  const pending = state.promotion?.counts.pending ?? 0
  const llmDecided = state.promotion?.by.llm ?? 0
  const rejected = state.promotion?.counts.rejected ?? 0
  const userPinned = state.promotion?.by.user ?? 0
  const promptEmpty = state.promotionPromptConfigured === false
  const anyBusy = busyKey !== null

  return (
    <div className="space-y-2.5">
      <p className="text-[11.5px] text-slate-400 leading-relaxed">
        启发式 + Agent 自动剔除无效候选；人工抽查后再「确认 Agent 剔除」锁定。
      </p>

      {/* Counts strip */}
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px]">
        <Stat label="待评" value={pending} tone="amber" />
        <Stat label="已选" value={state.promotion?.counts.promoted ?? 0} tone="emerald" />
        <Stat label="淘汰" value={rejected} tone="rose" />
        <span className="text-slate-700">·</span>
        <Stat label="human" value={userPinned} tone="slate" icon={<Hand size={9} />} />
        <Stat label="agent" value={llmDecided} tone="slate" icon={<Bot size={9} />} />
      </div>

      {/* Primary action: run promotion */}
      <div className="grid grid-cols-2 gap-2">
        <ActionButton
          onClick={onRun}
          icon={<Zap size={12} />}
          variant="primary"
          disabled={anyBusy}
          loading={busyKey === 'curate-run'}
          loadingLabel="剔除中"
          title="对所有候选节点跑启发式 + Agent 剔除"
        >
          自动剔除
        </ActionButton>
        <ActionButton
          onClick={onAccept}
          icon={<ShieldCheck size={12} />}
          variant="ghost"
          disabled={llmDecided === 0 || anyBusy}
          loading={busyKey === 'curate-accept'}
          loadingLabel="确认中"
          title="把 Agent 的剔除结果锁成 human 确定"
        >
          确认 Agent {llmDecided > 0 ? `(${llmDecided})` : ''}
        </ActionButton>
      </div>

      {/* Promotion settings */}
      <div className="flex flex-wrap gap-x-3 gap-y-1.5 text-[11px] text-slate-400">
        <label
          className="inline-flex items-center gap-1 cursor-pointer"
          title={
            promptEmpty
              ? '提示词未配置，即使勾上 Agent 也会被跳过'
              : '不勾时只跑启发式，速度快但灰色地带保留待修订'
          }
        >
          <input
            type="checkbox"
            checked={useLlm}
            onChange={e => setUseLlm(e.target.checked)}
            className="accent-indigo-500 h-3 w-3"
          />
          <span className={promptEmpty ? 'text-amber-300/90' : undefined}>
            调用 Agent{promptEmpty ? '（未配置）' : ''}
          </span>
        </label>
        <label className="inline-flex items-center gap-1 cursor-pointer" title="忽略 30 天冷却期">
          <input
            type="checkbox"
            checked={forceAll}
            onChange={e => setForceAll(e.target.checked)}
            className="accent-indigo-500 h-3 w-3"
          />
          <span>强制重剔</span>
        </label>
        <button
          onClick={onOpenPromptEditor}
          className="inline-flex items-center gap-1 text-slate-400 hover:text-slate-200"
          title="编辑发给 Agent 的剔除提示词"
        >
          <Pencil size={10} /> 提示词
        </button>
      </div>

      {/* Candidate visibility mode */}
      <div className="pt-1.5">
        <div className="text-[10.5px] uppercase tracking-wider text-slate-500 mb-1">
          图谱可见范围
        </div>
        <div className="grid grid-cols-3 gap-1 rounded-lg bg-slate-950/60 p-1 border border-slate-800">
          {(
            [
              { id: 'off', label: '仅选中', icon: <EyeOff size={10} />, disabled: false },
              {
                id: 'pending',
                label: pending > 0 ? `候选(${pending})` : '候选',
                icon: <Eye size={10} />,
                disabled: pending === 0,
              },
              {
                id: 'all',
                label: '全量',
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
      </div>

      <ActionButton
        onClick={onOpenRescue}
        icon={<Trash2 size={11} />}
        variant="ghost"
        disabled={rejected === 0}
        title="打开回收站，召回误剔节点"
        className="w-full"
      >
        召回误剔{rejected > 0 ? ` (${rejected})` : ''}
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
        把 DB 里的论文与概念编译为可读的 wiki .md（被 Ask / 全文搜索使用）。
      </p>
      <div className="space-y-1.5">
        <CompileRow
          icon={<FileText size={12} />}
          label="论文页"
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
          label="概念页"
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
          {total === 0 ? '尚未编译' : `${ok}/${total}`}
        </span>
      </div>
      {issues > 0 && !runningHere && (
        <div className="mt-0.5 text-[10.5px] text-slate-500 tabular-nums">
          {missing > 0 && `${missing} 待编译 `}
          {stale > 0 && `${stale} 已过期 `}
          {orphan > 0 && `${orphan} 孤儿`}
        </div>
      )}
      {runningHere ? (
        <button
          disabled
          className="mt-1.5 w-full inline-flex items-center justify-center gap-1.5 text-[11px] px-2 py-1 rounded-md border border-indigo-500/40 bg-indigo-500/10 text-indigo-200 cursor-not-allowed"
        >
          <Loader2 size={11} className="animate-spin" />
          编译中…
        </button>
      ) : allOk ? (
        // Done state: green "up to date" pill. Stays until the next
        // freshness poll surfaces new missing/stale items, which flips
        // this back to the amber 重编译 button.
        <div className="mt-1.5 w-full inline-flex items-center justify-center gap-1.5 text-[11px] px-2 py-1 rounded-md border border-emerald-500/40 bg-emerald-500/10 text-emerald-300">
          <CheckCircle2 size={11} />
          已是最新
        </div>
      ) : total > 0 ? (
        <button
          onClick={onClick}
          disabled={disabled}
          className="mt-1.5 w-full inline-flex items-center justify-center gap-1.5 text-[11px] px-2 py-1 rounded-md border border-amber-500/40 bg-amber-500/10 text-amber-200 hover:bg-amber-500/20 disabled:opacity-50 transition-colors"
        >
          <Sparkles size={11} />
          {missing === total ? '编译' : '重编译'}
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
  const lint = state.lintStatus
  const compileTotalNodes =
    (state.freshness?.papers.total_processed ?? 0) +
    (state.freshness?.concepts.total_nodes ?? 0)
  const empty = compileTotalNodes === 0
  return (
    <div className="space-y-2">
      <p className="text-[11.5px] text-slate-400 leading-relaxed">
        扫描 wiki .md，找出短桩 / 可合并 / 待建概念 / 追问建议，并给出可执行操作。
      </p>
      <div className="grid grid-cols-1 gap-2">
        <ActionButton
          onClick={onOpenLint}
          icon={<Stethoscope size={12} />}
          variant="primary"
          disabled={empty}
          title={empty ? '尚无可检的编译内容' : '运行健康检查（自动 + Agent）'}
        >
          {lint?.exists ? '查看 / 重跑健康检查' : '运行健康检查'}
        </ActionButton>
        {lint?.exists && (
          <button
            onClick={onOpenLint}
            className="inline-flex items-center justify-center gap-1.5 text-[11px] text-slate-400 hover:text-slate-200 px-2 py-1 rounded-md border border-slate-700/80 bg-slate-900/40"
            title={lint.rel_path}
          >
            <FileSearch size={11} /> 查看最新报告
          </button>
        )}
      </div>
    </div>
  )
}

// --- shared subcomponents --------------------------------------------

function StageIcon({ stage, tone }: { stage: StageId; tone: StageSnapshot['tone'] }) {
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
  const cls =
    variant === 'primary'
      ? 'bg-indigo-500 hover:bg-indigo-400 text-white border-indigo-400/60'
      : 'bg-slate-900/60 hover:bg-slate-800 text-slate-200 border-slate-700'
  return (
    <button
      onClick={onClick}
      disabled={disabled || loading}
      title={title}
      className={`inline-flex items-center justify-center gap-1.5 text-[11.5px] font-medium px-2.5 py-1.5 rounded-lg border transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${cls} ${className || ''}`}
    >
      {loading ? <Loader2 size={12} className="animate-spin" /> : icon}
      {loading && loadingLabel ? loadingLabel : children}
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
          {errors > 0 ? ` · ${errors} 失败` : ''}
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
      return '准备中'
    case 'uploading':
      return '上传中'
    case 'committing':
      return '提交中'
    case 'done':
      return '提交完成'
    case 'error':
      return '同步失败'
    default:
      return '同步中'
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
