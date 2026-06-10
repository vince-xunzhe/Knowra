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
 *   - Otherwise → "立即同步" button. v1 sends an empty snapshot
 *     (effectively a heartbeat) since the local snapshot builder isn't
 *     wired yet; the button label makes that clear so the user knows
 *     this is a wiring check, not real data movement.
 */
import { useMemo, useState } from 'react'
import {
  CloudUpload, CloudOff, Loader2, CheckCircle2, AlertTriangle, RefreshCw, RotateCw,
} from 'lucide-react'

import { useCloudAuth } from '../hooks/useCloudAuth'
import {
  getPendingResume,
  resumeCommit,
  runSync,
  type SyncProgress,
} from '../services/syncAgent'
import { gatherLocalSnapshot } from '../services/gatherLocalSnapshot'
import { getLastSyncAt } from '../api/cloud'

interface Props {
  expanded: boolean
  onToggle: () => void
}

export default function SyncStageCard({ expanded, onToggle }: Props) {
  const auth = useCloudAuth()
  const [progress, setProgress] = useState<SyncProgress>({
    stage: 'idle', uploadsDone: 0, uploadsTotal: 0, uploadsSkipped: 0,
  })
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
      await runSync(snapshot, setProgress)
    } catch (err) {
      const message = (err as Error).message || '同步失败'
      setProgress(p => ({ ...p, stage: 'error', error: message }))
    }
  }

  const handleResume = async () => {
    try {
      await resumeCommit(setProgress)
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
        className="w-full px-3 py-2 flex items-center gap-2 text-left"
      >
        <span className={`text-[12px] font-mono tabular-nums ${palette.indexColor}`}>
          ⑤
        </span>
        <SyncIcon tone={tone} />
        <span className="text-[13px] font-semibold text-slate-100">同步</span>
        <span className="ml-auto text-[11.5px] tabular-nums text-slate-300 truncate max-w-[8rem]">
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

              {progress.stage === 'error' && progress.error && (
                <div className="px-2 py-1.5 rounded-md border border-rose-500/40 bg-rose-500/10 text-[11px] text-rose-200">
                  {progress.error}
                </div>
              )}

              {progress.stage === 'done' && progress.commit && (
                progress.commit.rejected && progress.commit.rejected.length > 0 ? (
                  <div className="px-2 py-1.5 rounded-md border border-amber-500/40 bg-amber-500/10 text-[11px] text-amber-200 space-y-1">
                    <div>
                      ⚠ 已同步 revision {progress.commit.revision} · papers{' '}
                      {progress.commit.accepted.papers} / nodes{' '}
                      {progress.commit.accepted.knowledge_nodes} / edges{' '}
                      {progress.commit.accepted.knowledge_edges} / wiki{' '}
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
                    ✓ 已同步 · revision {progress.commit.revision} ·
                    {' '}papers {progress.commit.accepted.papers}
                    {' '}/ nodes {progress.commit.accepted.knowledge_nodes}
                    {' '}/ edges {progress.commit.accepted.knowledge_edges}
                    {' '}/ wiki {progress.commit.accepted.wiki_files}
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
