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
// hook's nextStep state machine. Whole rail collapses to a thin 48-px
// icon strip for users who don't need it.

import { useState } from 'react'
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
} from 'lucide-react'
import type {
  PipelineActions,
  PipelineState,
  StageId,
  StageSnapshot,
} from '../hooks/usePipelineState'
import PromotionPromptEditor from './PromotionPromptEditor'

type CandidateMode = 'off' | 'pending' | 'all'

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
  const [collapsed, setCollapsed] = useState(false)
  const [expanded, setExpanded] = useState<StageId | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [promptEditorOpen, setPromptEditorOpen] = useState(false)
  // Local toggles that live inside the curate stage but persist across
  // re-renders within the page.
  const [useLlm, setUseLlm] = useState(true)
  const [forceAll, setForceAll] = useState(false)

  const safeRun = async (fn: () => Promise<unknown>) => {
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  // Auto-expand the stage that nextStep is pointing at, so the user lands
  // on the relevant CTA without an extra click. Manual expand/collapse
  // overrides this.
  const effectiveExpanded: StageId | null =
    expanded != null ? expanded : state.nextStep?.stage ?? null

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
            onClick={() => {
              setCollapsed(false)
              setExpanded(s.id)
            }}
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

      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-2">
        {state.stages.map(stage => (
          <StageCard
            key={stage.id}
            stage={stage}
            expanded={effectiveExpanded === stage.id}
            onToggle={() =>
              setExpanded(prev => (prev === stage.id ? null : stage.id))
            }
          >
            {stage.id === 'ingest' && (
              <IngestActions
                state={state}
                onScan={() => safeRun(state.scan)}
                onProcess={() => safeRun(state.process)}
              />
            )}
            {stage.id === 'curate' && (
              <CurateActions
                state={state}
                useLlm={useLlm}
                setUseLlm={setUseLlm}
                forceAll={forceAll}
                setForceAll={setForceAll}
                candidateMode={candidateMode}
                onCandidateModeChange={onCandidateModeChange}
                onOpenRescue={onOpenRescue}
                onOpenPromptEditor={() => setPromptEditorOpen(true)}
                onRun={() =>
                  safeRun(() =>
                    state.runPromotionRun({ use_llm: useLlm, force_all: forceAll }),
                  )
                }
                onAccept={() => safeRun(state.acceptPromotion)}
              />
            )}
            {stage.id === 'compile' && (
              <CompileActions
                state={state}
                onCompilePapers={() => safeRun(state.recompilePapers)}
                onCompileConcepts={() => safeRun(state.recompileConcepts)}
              />
            )}
            {stage.id === 'maintain' && (
              <MaintainActions state={state} onOpenLint={onOpenLint} />
            )}
          </StageCard>
        ))}

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
  children: React.ReactNode
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
        className="w-full px-3 py-2 flex items-center gap-2 text-left"
      >
        <span className={`text-[12px] font-mono tabular-nums ${palette.indexColor}`}>
          {stage.index}
        </span>
        <StageIcon stage={stage.id} tone={stage.tone} />
        <span className="text-[13px] font-semibold text-slate-100">
          {stage.label}
        </span>
        {stage.isNext && (
          <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-indigo-500/20 text-indigo-200 border border-indigo-400/40 uppercase tracking-wider">
            建议
          </span>
        )}
        <span className="ml-auto text-[11.5px] tabular-nums text-slate-300">
          {stage.headline}
        </span>
      </button>
      {stage.sub && (
        <div className="px-3 -mt-1 pb-2 text-[11px] text-slate-400 leading-relaxed">
          {stage.sub}
        </div>
      )}
      {expanded && (
        <div className="px-3 pb-3 pt-1 border-t border-slate-800/40">
          {children}
        </div>
      )}
    </section>
  )
}

// --- Stage ① 录入 ----------------------------------------------------

function IngestActions({
  state,
  onScan,
  onProcess,
}: {
  state: PipelineState & PipelineActions
  onScan: () => void
  onProcess: () => void
}) {
  const running = !!state.processing?.running
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
          disabled={running}
          title="扫描 data/papers 目录，找出未入库的 PDF"
        >
          扫描目录
        </ActionButton>
        <ActionButton
          onClick={onProcess}
          icon={running ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
          variant="primary"
          disabled={running}
          title={state.processing?.current || '处理所有未入库论文'}
        >
          {running ? '处理中' : remaining > 0 ? `处理 ${remaining} 篇` : '处理论文'}
        </ActionButton>
      </div>
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
          title="对所有候选节点跑启发式 + Agent 剔除"
        >
          自动剔除
        </ActionButton>
        <ActionButton
          onClick={onAccept}
          icon={<ShieldCheck size={12} />}
          variant="ghost"
          disabled={llmDecided === 0}
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
  onCompilePapers,
  onCompileConcepts,
}: {
  state: PipelineState & PipelineActions
  onCompilePapers: () => void
  onCompileConcepts: () => void
}) {
  const running = !!state.compileStatus?.running
  const runningKind = state.compileStatus?.kind
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
          runningHere={running && runningKind === 'papers'}
          progress={state.compileStatus}
          onClick={onCompilePapers}
          disabled={running || paperIssues === 0}
        />
        <CompileRow
          icon={<BookMarked size={12} />}
          label="概念页"
          ok={f?.concepts.ok ?? 0}
          total={f?.concepts.total_nodes ?? 0}
          missing={f?.concepts.missing_count ?? 0}
          stale={f?.concepts.stale_count ?? 0}
          orphan={f?.concepts.orphan_count ?? 0}
          runningHere={running && runningKind === 'concepts'}
          progress={state.compileStatus}
          onClick={onCompileConcepts}
          disabled={running || conceptIssues === 0}
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
  icon: React.ReactNode
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
  const cta = total > 0 && (issues > 0 || total !== ok)
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/60 px-2.5 py-1.5">
      <div className="flex items-center gap-2">
        <span className="text-slate-400">{icon}</span>
        <span className="text-[12px] text-slate-200 font-medium">{label}</span>
        <span className="ml-auto text-[11px] tabular-nums text-slate-400">
          {total === 0 ? '尚未编译' : `${ok}/${total}`}
        </span>
      </div>
      {issues > 0 && (
        <div className="mt-0.5 text-[10.5px] text-slate-500 tabular-nums">
          {missing > 0 && `${missing} 待编译 `}
          {stale > 0 && `${stale} 已过期 `}
          {orphan > 0 && `${orphan} 孤儿`}
        </div>
      )}
      {cta && (
        <button
          onClick={onClick}
          disabled={disabled}
          className="mt-1.5 w-full inline-flex items-center justify-center gap-1.5 text-[11px] px-2 py-1 rounded-md border border-amber-500/40 bg-amber-500/10 text-amber-200 hover:bg-amber-500/20 disabled:opacity-50 transition-colors"
        >
          {runningHere ? (
            <Loader2 size={11} className="animate-spin" />
          ) : (
            <Sparkles size={11} />
          )}
          {runningHere ? '编译中…' : missing === total ? '编译' : '重编译'}
        </button>
      )}
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
  title,
  className,
  children,
}: {
  onClick: () => void
  icon: React.ReactNode
  variant: 'primary' | 'ghost'
  disabled?: boolean
  title?: string
  className?: string
  children: React.ReactNode
}) {
  const cls =
    variant === 'primary'
      ? 'bg-indigo-500 hover:bg-indigo-400 text-white border-indigo-400/60'
      : 'bg-slate-900/60 hover:bg-slate-800 text-slate-200 border-slate-700'
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`inline-flex items-center justify-center gap-1.5 text-[11.5px] font-medium px-2.5 py-1.5 rounded-lg border transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${cls} ${className || ''}`}
    >
      {icon}
      {children}
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
  icon?: React.ReactNode
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

// Type re-exports for the rare case a consumer needs them. Most consumers
// should just hand a `state` prop returned by usePipelineState().
export type { StageId, StageSnapshot, CandidateMode }
// Re-export shared icons so consumers can build icon-only previews without
// importing both lucide-react and this file directly.
export { CheckCircle2, AlertTriangle, Loader2 }
