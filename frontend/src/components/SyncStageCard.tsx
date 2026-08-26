/**
 * ⑤ 同步 — appended to PipelineConsole after the existing 4 stages.
 *
 * Renders the same visual shell as the other stage cards (rounded
 * border + index + headline) but is intentionally NOT plumbed through
 * usePipelineState — sync is a cross-cutting export, not a stage in
 * the local ingest pipeline. Keeping it separate avoids tangling the
 * next-step state machine with cloud-auth state.
 *
 * Behavior:
 *   - If not logged in (no Supabase session) → muted card pointing the
 *     user to Settings → 云同步.
 *   - If a half-finished prepare/upload is parked in localStorage from
 *     a previous crash → offer a "继续提交" button that re-runs only
 *     commit.
 *   - Otherwise → "立即同步" button. It snapshots the local SQLite/wiki
 *     state and pushes it to the cloud.
 */
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  CloudUpload, CloudOff, Loader2, CheckCircle2, AlertTriangle, RefreshCw, RotateCw,
  ChevronDown, ChevronRight, Database, HardDrive, Server,
} from 'lucide-react'

import { useCloudAuth } from '../hooks/useCloudAuth'
import {
  getPendingResume,
  resumeCommit,
  runSync,
  type SyncProgress,
} from '../services/syncAgent'
import { gatherLocalSnapshot, previewSnapshotCounts } from '../services/gatherLocalSnapshot'
import { cloudMe, getLastSyncAt, type MeResponse } from '../api/cloud'

interface Props {
  expanded: boolean
  onToggle: () => void
}

export default function SyncStageCard({ expanded, onToggle }: Props) {
  const auth = useCloudAuth()
  const [progress, setProgress] = useState<SyncProgress>({
    stage: 'idle', uploadsDone: 0, uploadsTotal: 0, uploadsSkipped: 0,
  })
  const [localPreview, setLocalPreview] = useState<{
    loading: boolean
    data: SnapshotCounts | null
    error: string | null
    checkedAt: number | null
  }>({ loading: false, data: null, error: null, checkedAt: null })
  const [cloudStatus, setCloudStatus] = useState<{
    loading: boolean
    data: MeResponse | null
    error: string | null
    checkedAt: number | null
  }>({ loading: false, data: null, error: null, checkedAt: null })
  // Read straight from localStorage every render — both are sub-µs and
  // the component is small. Storing them in useState + syncing through
  // useEffect tripped react-hooks/set-state-in-effect; useMemo also
  // didn't satisfy exhaustive-deps. Inline reads are the simplest fix.
  const pending = getPendingResume()
  const lastSyncAt = getLastSyncAt()

  const running =
    progress.stage === 'preparing' ||
    progress.stage === 'uploading' ||
    progress.stage === 'committing'

  const tone: 'idle' | 'running' | 'ok' | 'warning' | 'danger' = !auth.user
    ? 'warning'
    : progress.stage === 'error'
      ? 'danger'
      : running
        ? 'running'
        : progress.stage === 'done'
          ? 'ok'
    : pending
            ? 'warning'
            : 'idle'

  const palette = paletteFor(tone)

  const refreshCloudStatus = useCallback(async () => {
    if (!auth.user) return
    setCloudStatus(s => ({ ...s, loading: true, error: null }))
    try {
      const data = await cloudMe()
      setCloudStatus({ loading: false, data, error: null, checkedAt: Date.now() })
    } catch (err) {
      const message = (err as Error).message || '读取云端状态失败'
      setCloudStatus(s => ({ ...s, loading: false, error: message, checkedAt: Date.now() }))
    }
  }, [auth.user])

  const refreshLocalPreview = useCallback(async () => {
    setLocalPreview(s => ({ ...s, loading: true, error: null }))
    try {
      const data = await previewSnapshotCounts()
      setLocalPreview({ loading: false, data, error: null, checkedAt: Date.now() })
    } catch (err) {
      const message = (err as Error).message || '读取本机快照失败'
      setLocalPreview(s => ({ ...s, loading: false, error: message, checkedAt: Date.now() }))
    }
  }, [])

  useEffect(() => {
    if (!expanded || !auth.user || cloudStatus.data || cloudStatus.loading) return
    const timer = window.setTimeout(() => void refreshCloudStatus(), 0)
    return () => window.clearTimeout(timer)
  }, [expanded, auth.user, cloudStatus.data, cloudStatus.loading, refreshCloudStatus])

  useEffect(() => {
    if (!expanded || localPreview.data || localPreview.loading) return
    const timer = window.setTimeout(() => void refreshLocalPreview(), 0)
    return () => window.clearTimeout(timer)
  }, [expanded, localPreview.data, localPreview.loading, refreshLocalPreview])

  const headline = useMemo(() => {
    if (!auth.configured) return '未配置'
    if (!auth.user) return '未登录'
    if (running) {
      if (progress.stage === 'uploading' && progress.uploadsTotal > 0) {
        return `上传 ${progress.uploadsDone}/${progress.uploadsTotal}`
      }
      return stageLabel(progress.stage)
    }
    if (pending) return '待续传'
    return lastSyncAt ? new Date(lastSyncAt).toLocaleString() : '从未同步'
  }, [auth.configured, auth.user, running, progress, pending, lastSyncAt])

  const handleRun = async () => {
    try {
      setProgress({ stage: 'preparing', uploadsDone: 0, uploadsTotal: 0, uploadsSkipped: 0 })
      const snapshot = await gatherLocalSnapshot({ since: lastSyncAt })
      setLocalPreview({
        loading: false,
        error: null,
        checkedAt: Date.now(),
        data: {
          papers: snapshot.papers.length,
          knowledge_nodes: snapshot.knowledge_nodes.length,
          knowledge_edges: snapshot.knowledge_edges.length,
          wiki_files: snapshot.wiki_files.length,
        },
      })
      await runSync(snapshot, setProgress)
      await refreshCloudStatus()
    } catch (err) {
      const message = (err as Error).message || '同步失败'
      setProgress(p => ({ ...p, stage: 'error', error: message }))
    }
  }

  const handleResume = async () => {
    try {
      await resumeCommit(setProgress)
      await refreshCloudStatus()
    } catch {
      // progress already carries it
    }
  }

  return (
    <section
      className={`rounded-xl border transition-all ${palette.border} ${palette.bg}`}
    >
      <button
        onClick={onToggle}
        aria-expanded={expanded}
        title={expanded ? '收起同步' : '展开同步'}
        className="grid w-full grid-cols-[auto_minmax(0,1fr)] items-center gap-2 px-3 py-2.5 text-left"
      >
        <span className="flex min-w-0 items-center gap-2">
          {expanded ? (
            <ChevronDown size={13} className="shrink-0 text-slate-500" />
          ) : (
            <ChevronRight size={13} className="shrink-0 text-slate-600" />
          )}
          <span className={`shrink-0 text-[12px] font-mono tabular-nums ${palette.indexColor}`}>
            ⑤
          </span>
          <SyncIcon tone={tone} />
          <span className="shrink-0 whitespace-nowrap text-[13px] font-semibold text-slate-100">同步</span>
        </span>
        <span
          className="min-w-0 justify-self-end truncate whitespace-nowrap text-right text-[11.5px] tabular-nums text-slate-300"
          title={headline}
        >
          {headline}
        </span>
      </button>

      {expanded && (
        <div className="px-3 pb-3 pt-1 border-t border-slate-800/40 space-y-2">
          {!auth.configured ? (
            <p className="text-[11.5px] text-amber-200/90 leading-relaxed">
              请到 <span className="font-semibold">设置 → 云同步</span> 填写 Supabase URL / anon key / 云后端 URL。
            </p>
          ) : !auth.user ? (
            <p className="text-[11.5px] text-amber-200/90 leading-relaxed">
              请到 <span className="font-semibold">设置 → 云同步</span> 登录云端账号后，才能把本地数据推送上去。
            </p>
          ) : (
            <>
              <p className="text-[11.5px] text-slate-400 leading-relaxed">
                把本地论文 / 知识节点 / 编译好的 wiki 同步到云后端，供 iOS / Android 只读消费。
                PDF 永远只在本机；OpenAI key 也不上传。
              </p>

              <SnapshotStatusPanel
                status={localPreview}
                onRefresh={refreshLocalPreview}
              />

              <CloudStatusPanel
                status={cloudStatus}
                onRefresh={refreshCloudStatus}
              />

              {progress.stage === 'error' && progress.error && (
                <div className="px-2 py-1.5 rounded-md border border-rose-500/40 bg-rose-500/10 text-[11px] text-rose-200">
                  {progress.error}
                </div>
              )}

              {progress.stage === 'done' && progress.commit && (
                progress.commit.rejected && progress.commit.rejected.length > 0 ? (
                  <div className="px-2 py-1.5 rounded-md border border-amber-500/40 bg-amber-500/10 text-[11px] text-amber-200 space-y-1">
                    <div>
                      ⚠ 本次写入/更新 · revision {progress.commit.revision} · 论文{' '}
                      {progress.commit.accepted.papers} / 节点{' '}
                      {progress.commit.accepted.knowledge_nodes} / 关系{' '}
                      {progress.commit.accepted.knowledge_edges} / Wiki{' '}
                      {progress.commit.accepted.wiki_files}
                    </div>
                    <div className="text-amber-300/90">
                      {progress.commit.rejected.length} 个文件云端未收，下次同步会重试：
                    </div>
                    <ul className="font-mono text-[10px] text-amber-100/80 space-y-0.5 max-h-24 overflow-y-auto">
                      {progress.commit.rejected.slice(0, 6).map((r, i) => (
                        <li key={i} className="truncate" title={r.reason}>
                          {r.rel_path || r.table}（{r.code}）
                        </li>
                      ))}
                      {progress.commit.rejected.length > 6 && (
                        <li>… 等共 {progress.commit.rejected.length} 项</li>
                      )}
                    </ul>
                  </div>
                ) : (
                  <div className="px-2 py-1.5 rounded-md border border-emerald-500/30 bg-emerald-500/10 text-[11px] text-emerald-200">
                    ✓ 本次写入/更新 · revision {progress.commit.revision} ·
                    {' '}论文 {progress.commit.accepted.papers}
                    {' '}/ 节点 {progress.commit.accepted.knowledge_nodes}
                    {' '}/ 关系 {progress.commit.accepted.knowledge_edges}
                    {' '}/ Wiki {progress.commit.accepted.wiki_files}
                  </div>
                )
              )}

              {running && progress.currentFile && (
                <div className="px-2 py-1.5 rounded-md bg-slate-900/50 text-[11px] text-slate-300 font-mono truncate">
                  {progress.currentFile}
                </div>
              )}

              <div className="grid grid-cols-2 gap-2">
                <button
                  onClick={handleRun}
                  disabled={running}
                  className="inline-flex items-center justify-center gap-1.5 text-[12px] px-2.5 py-1.5 rounded-lg bg-indigo-500/15 hover:bg-indigo-500/25 text-indigo-100 border border-indigo-500/40 disabled:opacity-50 disabled:cursor-not-allowed"
                  title="跑一次 prepare → upload → commit"
                >
                  {running
                    ? <Loader2 size={12} className="animate-spin" />
                    : <CloudUpload size={12} />}
                  {running ? stageLabel(progress.stage) : '立即同步'}
                </button>

                {pending && (
                  <button
                    onClick={handleResume}
                    disabled={running}
                    className="inline-flex items-center justify-center gap-1.5 text-[12px] px-2.5 py-1.5 rounded-lg bg-amber-500/10 hover:bg-amber-500/20 text-amber-200 border border-amber-500/40 disabled:opacity-50"
                    title={`续传上次的 session ${pending.sync_session_id.slice(0, 8)}…（已上传 ${pending.uploaded_count} 个文件）`}
                  >
                    <RotateCw size={12} />
                    继续提交
                  </button>
                )}
              </div>

              {lastSyncAt && (
                <div className="text-[10.5px] text-slate-500 flex items-center gap-1.5">
                  <RefreshCw size={10} />
                  上次成功：{new Date(lastSyncAt).toLocaleString()}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </section>
  )
}

type SnapshotCounts = {
  papers: number
  knowledge_nodes: number
  knowledge_edges: number
  wiki_files: number
}

function SnapshotStatusPanel({
  status,
  onRefresh,
}: {
  status: {
    loading: boolean
    data: SnapshotCounts | null
    error: string | null
    checkedAt: number | null
  }
  onRefresh: () => Promise<void>
}) {
  const checkedAt = status.checkedAt ? new Date(status.checkedAt).toLocaleTimeString() : null

  return (
    <div className="rounded-lg border border-slate-800/80 bg-slate-950/45 px-2.5 py-2 space-y-2">
      <PanelHeader
        icon={<HardDrive size={11} className={status.error ? 'text-rose-300' : 'text-indigo-300'} />}
        title={status.loading ? '正在读取本机快照' : status.error ? '本机快照读取失败' : '本机待提交快照'}
        checkedAt={checkedAt}
        loading={status.loading}
        onRefresh={onRefresh}
        refreshTitle="刷新本机快照"
      />

      {status.error ? (
        <div className="rounded-md border border-rose-500/30 bg-rose-500/10 px-2 py-1.5 text-[10.5px] text-rose-200">
          {status.error}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-1.5">
          <CloudMetric icon={<Database size={11} />} label="论文行" value={status.data ? String(status.data.papers) : '...'} />
          <CloudMetric icon={<Database size={11} />} label="节点" value={status.data ? String(status.data.knowledge_nodes) : '...'} />
          <CloudMetric icon={<Database size={11} />} label="关系" value={status.data ? String(status.data.knowledge_edges) : '...'} />
          <CloudMetric icon={<HardDrive size={11} />} label="Wiki 文件" value={status.data ? String(status.data.wiki_files) : '...'} />
        </div>
      )}
    </div>
  )
}

function CloudStatusPanel({
  status,
  onRefresh,
}: {
  status: {
    loading: boolean
    data: MeResponse | null
    error: string | null
    checkedAt: number | null
  }
  onRefresh: () => Promise<void>
}) {
  const stats = status.data?.stats
  const checkedAt = status.checkedAt ? new Date(status.checkedAt).toLocaleTimeString() : null
  const lastCloudSync = stats?.last_desktop_sync_at
    ? new Date(stats.last_desktop_sync_at).toLocaleString()
    : '暂无'

  return (
    <div className="rounded-lg border border-slate-800/80 bg-slate-950/45 px-2.5 py-2 space-y-2">
      <PanelHeader
        icon={<Server size={11} className={status.error ? 'text-rose-300' : 'text-emerald-300'} />}
        title={status.loading ? '正在读取云端总量' : status.error ? '云端状态读取失败' : '云端总量'}
        checkedAt={checkedAt}
        loading={status.loading}
        onRefresh={onRefresh}
        refreshTitle="刷新云端状态"
      />

      {status.error ? (
        <div className="rounded-md border border-rose-500/30 bg-rose-500/10 px-2 py-1.5 text-[10.5px] text-rose-200">
          {status.error}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-1.5">
          <CloudMetric icon={<Database size={11} />} label="去重论文" value={stats ? String(stats.papers) : '...'} />
          <CloudMetric icon={<Database size={11} />} label="节点" value={stats ? String(stats.nodes ?? stats.concepts) : '...'} />
          <CloudMetric icon={<Database size={11} />} label="关系" value={stats ? String(stats.edges) : '...'} />
          <CloudMetric icon={<HardDrive size={11} />} label="Wiki 文件" value={stats ? `${stats.wiki_files} · ${formatBytes(stats.wiki_size_bytes)}` : '...'} />
        </div>
      )}

      <div className="flex items-center justify-between gap-2 text-[10.5px] text-slate-500">
        <span className="truncate" title={status.data?.email || undefined}>
          {status.data?.email || status.data?.user_id || '已登录账号'}
        </span>
        <span className="shrink-0 tabular-nums" title={lastCloudSync}>
          云端记录：{lastCloudSync}
        </span>
      </div>
    </div>
  )
}

function PanelHeader({
  icon,
  title,
  checkedAt,
  loading,
  onRefresh,
  refreshTitle,
}: {
  icon: ReactNode
  title: string
  checkedAt: string | null
  loading: boolean
  onRefresh: () => Promise<void>
  refreshTitle: string
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <div className="flex min-w-0 items-center gap-1.5 text-[11px] text-slate-300">
        {icon}
        <span className="truncate">{title}</span>
        {checkedAt && <span className="shrink-0 text-slate-600">· {checkedAt}</span>}
      </div>
      <button
        onClick={() => void onRefresh()}
        disabled={loading}
        className="inline-flex shrink-0 items-center gap-1 rounded-md border border-slate-700/80 px-1.5 py-0.5 text-[10.5px] text-slate-400 hover:border-slate-500 hover:text-slate-200 disabled:opacity-50"
        title={refreshTitle}
      >
        <RefreshCw size={10} className={loading ? 'animate-spin' : ''} />
        刷新
      </button>
    </div>
  )
}

function CloudMetric({
  icon,
  label,
  value,
}: {
  icon: ReactNode
  label: string
  value: string
}) {
  return (
    <div className="min-w-0 rounded-md border border-slate-800/70 bg-slate-900/45 px-2 py-1.5">
      <div className="flex items-center gap-1 text-[10px] text-slate-500">
        {icon}
        <span>{label}</span>
      </div>
      <div className="mt-0.5 truncate text-[12px] font-medium tabular-nums text-slate-200" title={value}>
        {value}
      </div>
    </div>
  )
}

function formatBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex += 1
  }
  const digits = value >= 10 || unitIndex === 0 ? 0 : 1
  return `${value.toFixed(digits)} ${units[unitIndex]}`
}

function stageLabel(stage: SyncProgress['stage']): string {
  switch (stage) {
    case 'preparing': return '准备中'
    case 'uploading': return '上传中'
    case 'committing': return '提交中'
    case 'done': return '完成'
    case 'error': return '错误'
    default: return '空闲'
  }
}

function SyncIcon({ tone }: { tone: 'idle' | 'running' | 'ok' | 'warning' | 'danger' }) {
  const base = 'shrink-0'
  if (tone === 'running') return <Loader2 size={14} className={`${base} text-indigo-300 animate-spin`} />
  if (tone === 'ok')      return <CheckCircle2 size={14} className={`${base} text-emerald-300`} />
  if (tone === 'danger')  return <AlertTriangle size={14} className={`${base} text-rose-300`} />
  if (tone === 'warning') return <CloudOff size={14} className={`${base} text-amber-300`} />
  return <CloudUpload size={14} className={`${base} text-slate-400`} />
}

function paletteFor(tone: 'idle' | 'running' | 'ok' | 'warning' | 'danger') {
  if (tone === 'running') return { border: 'border-indigo-500/40', bg: 'bg-indigo-500/5', indexColor: 'text-indigo-300' }
  if (tone === 'ok')      return { border: 'border-emerald-500/30', bg: 'bg-emerald-500/5', indexColor: 'text-emerald-300' }
  if (tone === 'danger')  return { border: 'border-rose-500/40', bg: 'bg-rose-500/5', indexColor: 'text-rose-300' }
  if (tone === 'warning') return { border: 'border-amber-500/30', bg: 'bg-amber-500/5', indexColor: 'text-amber-300' }
  return { border: 'border-slate-800', bg: 'bg-slate-900/30', indexColor: 'text-slate-500' }
}
