import { useEffect, useState } from 'react'
import { Activity, ChevronDown, RotateCcw } from 'lucide-react'
import { listJobs, resumeJob, type BackgroundJob } from '../api/jobs'

const names: Record<string, string> = { scan: '扫描目录', papers: '论文处理', compile: 'Wiki 编译', promotion: '概念筛选', lint: '健康检查', pipeline: '全流程编排' }
const labels = { queued: '排队中', running: '执行中', completed: '已完成', failed: '失败，可恢复', interrupted: '已中断，可恢复' }

export default function BackgroundTasks() {
  const [jobs, setJobs] = useState<BackgroundJob[]>([])
  const [open, setOpen] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      try {
        const next = await listJobs()
        if (!stopped) { setJobs(next); setError('') }
      } catch {
        if (!stopped) setError('状态连接暂时不可用；后台任务不会因此停止。')
      }
      if (!stopped) timer = setTimeout(() => { void poll() }, 3000)
    }
    void poll()
    return () => { stopped = true; clearTimeout(timer) }
  }, [])
  if (!jobs.length) return null
  const active = jobs.filter(job => ['queued', 'running'].includes(job.status)).length
  const recover = async (id: string) => {
    setBusy(true)
    try {
      await resumeJob(id)
      setJobs(await listJobs())
      setError('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally { setBusy(false) }
  }
  return <aside className="fixed bottom-3 left-1/2 -translate-x-1/2 z-40 max-w-[calc(100vw-8rem)] text-xs">
    {open && <div className="mb-2 w-80 max-w-full rounded-xl border border-slate-700 bg-slate-950 p-3 shadow-xl">
      <p className="mb-2 font-semibold">后台任务 · 页面关闭后仍继续执行</p>
      {error && <p role="status" className="mb-2 text-amber-300">{error}</p>}
      <div className="max-h-72 overflow-y-auto space-y-3">
        {jobs.map(job => <div key={job.job_id} className="border-t border-slate-800 pt-2">
          <div className="flex justify-between gap-2"><span>{names[job.kind] || job.kind}</span><span className="text-slate-400">{labels[job.status]}</span></div>
          <p className="mt-1 break-words text-slate-400">{job.progress.label || job.progress.state?.current}</p>
          {job.error && <p className="mt-1 break-words text-amber-300">{job.error}</p>}
          {job.worker_online === false && <p className="mt-1 text-amber-300">等待 worker 心跳；任务已保存。外部模式请确认 worker 服务已启动。</p>}
          {['failed', 'interrupted'].includes(job.status) && <button disabled={busy || active > 0} onClick={() => { void recover(job.job_id) }} className="mt-2 inline-flex items-center gap-1 rounded border border-slate-600 px-2 py-1 disabled:opacity-50"><RotateCcw size={12} />从检查点恢复</button>}
        </div>)}
      </div>
    </div>}
    <button onClick={() => setOpen(!open)} aria-expanded={open} className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-slate-200 shadow-lg"><Activity size={14} />后台任务{active ? ` · ${active} 个执行/排队中` : ''}<ChevronDown size={12} /></button>
  </aside>
}
