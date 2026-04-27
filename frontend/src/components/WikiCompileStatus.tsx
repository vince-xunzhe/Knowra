import { useEffect, useRef, useState } from 'react'
import { Loader2, Sparkles, FileText } from 'lucide-react'
import { getWikiStatus, type WikiCompileState } from '../api/client'

// Always-mounted floating chip, mirror of <ProcessingStatus> but for the
// wiki compile pipeline. Lives bottom-LEFT so it doesn't collide with the
// paper-processing chip on the bottom-right when both happen to run.
//
// Only renders when a compile is in progress. Polling cadence backs off
// exponentially on transient errors (axios timeout / vite proxy 502),
// because long OpenAI calls inside the compile thread occasionally make
// status polls miss their window.
export default function WikiCompileStatus() {
  const [status, setStatus] = useState<WikiCompileState | null>(null)
  const runningRef = useRef(false)

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    let consecutiveErrors = 0

    const isTransient = (err: unknown) => {
      const e = err as { code?: string; response?: { status?: number } }
      return e.code === 'ECONNABORTED'
        || e.response?.status === 502
        || e.response?.status === 504
    }

    const tick = async () => {
      let nextDelay = runningRef.current ? 2500 : 6000
      try {
        const s = await getWikiStatus()
        if (cancelled) return
        consecutiveErrors = 0
        setStatus(s)
        runningRef.current = s.running
      } catch (err) {
        consecutiveErrors += 1
        nextDelay = Math.min(12000, 2500 * Math.max(1, consecutiveErrors))
        if (!isTransient(err)) console.error('wiki status poll failed', err)
      } finally {
        if (!cancelled) timer = setTimeout(tick, nextDelay)
      }
    }
    void tick()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [])

  if (!status?.running) return null

  const total = Math.max(0, status.total)
  const done = Math.max(0, status.done)
  const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0
  const kindLabel = status.kind === 'papers' ? '论文页' : '概念页'
  const Icon = status.kind === 'papers' ? FileText : Sparkles

  return (
    <div className="fixed bottom-5 left-5 bg-slate-900/95 backdrop-blur-md border border-indigo-500/30 rounded-2xl p-4 w-72 shadow-2xl shadow-indigo-500/10 z-50 fade-in">
      <div className="flex items-center gap-2.5 mb-2.5">
        <Icon size={14} className="text-indigo-400" />
        <span className="text-sm text-slate-100 font-medium">编译 {kindLabel}</span>
        <span className="ml-auto text-sm font-mono tabular-nums text-indigo-300">
          {total > 0 ? `${pct}%` : '...'}
        </span>
      </div>

      <div className="h-1.5 w-full bg-slate-800 rounded-full overflow-hidden">
        <div
          className="h-full bg-indigo-400 transition-[width] duration-500 ease-out"
          style={{ width: total > 0 ? `${pct}%` : '40%' }}
        />
      </div>

      <div className="mt-2 flex items-center gap-2 text-[11px] text-slate-500">
        <Loader2 size={11} className="animate-spin text-indigo-300" />
        <span className="font-mono tabular-nums">{done}/{total || '?'}</span>
        {status.errors > 0 && (
          <span className="text-amber-300">失败 {status.errors}</span>
        )}
        {status.model && (
          <span className="ml-auto text-slate-600">{status.model}</span>
        )}
      </div>

      {status.current && (
        <p className="mt-1.5 text-[11px] text-slate-400 line-clamp-2 leading-relaxed text-safe-wrap">
          {status.current}
        </p>
      )}
    </div>
  )
}
