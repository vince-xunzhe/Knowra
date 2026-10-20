import { useEffect, useState } from 'react'
import { Activity, RotateCcw } from 'lucide-react'
import { listJobs, resumeJob, type BackgroundJob } from '../api/jobs'

const names: Record<string, string> = { domain: '索骥代连', roadmap: '识途调研', roadmap_import: '识途论文入库', scan: '扫描目录', papers: '论文处理', compile: 'Wiki 编译', promotion: '概念筛选', lint: '健康检查', pipeline: '全流程编排' }
const labels = { cancelled: '已取消', queued: '排队中', running: '执行中', completed: '已完成', failed: '失败，可恢复', interrupted: '已中断，可恢复' }

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
  return <aside className="relative z-40 mt-auto w-[5.75rem] text-xs" data-testid="background-tasks">
    {open && <div className="absolute bottom-0 left-[calc(100%+0.75rem)] w-80 max-w-[calc(100vw-8rem)] rounded-xl border border-slate-700 bg-slate-950 p-3 text-left shadow-xl" data-testid="background-tasks-panel">
      <p className="mb-2 font-semibold">后台任务 · 页面关闭后仍继续执行</p>
      {error && <p role="status" className="mb-2 text-amber-300">{error}</p>}
      <div className="max-h-72 overflow-y-auto space-y-3">
        {jobs.map(job => <div key={job.job_id} className="border-t border-slate-800 pt-2">
          <div className="flex justify-between gap-2"><span>{names[job.kind] || job.kind}</span><span className="text-slate-400">{['domain', 'roadmap'].includes(job.kind) && ['failed', 'interrupted'].includes(job.status) ? '需要重新生成' : labels[job.status]}</span></div>
          <p className="mt-1 break-words text-slate-400">{job.progress.label || job.progress.state?.current}</p>
          {job.error && <p className="mt-1 break-words text-amber-300">{job.error}</p>}
          {job.worker_online === false && <p className="mt-1 text-amber-300">等待 worker 心跳；任务已保存。外部模式请确认 worker 服务已启动。</p>}
          {job.kind === 'domain' && ['failed', 'interrupted'].includes(job.status) && <p>请在索骥重新生成</p>}
          {job.kind === 'roadmap' && ['failed', 'interrupted'].includes(job.status) && <p>请在识途重新调研</p>}
          {job.kind === 'roadmap_import' && ['failed', 'interrupted'].includes(job.status) && <p>请在识途重试未入库论文</p>}
          {!['domain', 'roadmap', 'roadmap_import'].includes(job.kind) && ['failed', 'interrupted'].includes(job.status) && <button disabled={busy || active > 0} onClick={() => { void recover(job.job_id) }} className="mt-2 inline-flex items-center gap-1 rounded border border-slate-600 px-2 py-1 disabled:opacity-50"><RotateCcw size={12} />从检查点恢复</button>}
        </div>)}
      </div>
    </div>}
    <button
      onClick={() => setOpen(!open)}
      aria-expanded={open}
      title={active ? `后台任务 · ${active} 个执行/排队中` : '后台任务'}
      className={`group relative flex h-14 w-full flex-col items-center justify-center gap-1 rounded-2xl px-1 transition-all ${open ? 'bg-indigo-500/15 text-indigo-100 shadow-inner shadow-indigo-500/10' : 'text-slate-600 hover:bg-slate-800/50 hover:text-slate-100'}`}
    >
      <Activity size={18} strokeWidth={open ? 2.2 : 1.7} />
      <span className={`text-[10.5px] leading-none tracking-[0.02em] ${open ? 'text-indigo-100' : 'text-slate-600 group-hover:text-slate-300'}`}>后台任务</span>
      {active > 0 && <span className="absolute right-2 top-1.5 min-w-4 rounded-full bg-indigo-500 px-1 text-center text-[9px] font-semibold leading-4 text-white">{active}</span>}
      {open && <span className="absolute left-0 top-1/2 h-6 w-0.5 -translate-y-1/2 rounded-r bg-indigo-400" />}
    </button>
  </aside>
}
