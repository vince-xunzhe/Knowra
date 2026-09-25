import { useEffect } from 'react'
import { Loader2, Stethoscope, X } from 'lucide-react'
import { useWikiLint } from '../hooks/useWikiLint'

export default function WikiLintStatus({ onOpen }: { onOpen: () => void }) {
  const { job, connectionError, dismissedJobId, dismissNotification } = useWikiLint()
  const jobId = job?.job_id
  const status = job?.status
  useEffect(() => {
    if (!jobId || status !== 'completed' || dismissedJobId === jobId) return
    const timer = window.setTimeout(() => dismissNotification(jobId), 8000)
    return () => window.clearTimeout(timer)
  }, [jobId, status, dismissedJobId, dismissNotification])
  if (!job || !jobId || job.status === 'idle' ||
      (job.status !== 'running' && dismissedJobId === jobId)) return null
  const running = job.status === 'running'
  const warning = job.status === 'failed' || job.status === 'warning'
  return (
    <aside role="status" aria-live="polite" className="fixed bottom-5 left-24 z-30 w-72 rounded-xl border border-slate-700 bg-slate-900/95 p-3 shadow-xl backdrop-blur-md">
      <div className="flex items-center gap-2 text-sm font-medium text-slate-100">
        {running ? <Loader2 size={15} className="animate-spin text-indigo-300" /> : <Stethoscope size={15} className={warning ? 'text-amber-300' : 'text-emerald-300'} />}
        <span>{running ? '健康检查进行中' : job.phase}</span>
        {!running && <button aria-label="关闭健康检查提醒" className="ml-auto text-slate-400" onClick={() => dismissNotification(jobId)}><X size={14} /></button>}
      </div>
      <p className="mt-1 text-xs text-slate-400">{connectionError ? '状态连接暂时中断，正在重连…' : running ? `${job.phase}，可继续浏览。` : warning ? '请查看报告或重新运行检查。' : '报告已保存，可查看发现的问题。'}</p>
      <button onClick={() => { if (!running) dismissNotification(jobId); onOpen() }} className="mt-2 text-xs text-indigo-300 hover:text-indigo-200">{running ? '查看状态' : '查看报告'}</button>
    </aside>
  )
}
