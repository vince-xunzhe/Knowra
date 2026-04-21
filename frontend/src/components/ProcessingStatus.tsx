import { useEffect, useState } from 'react'
import { Loader2, AlertCircle } from 'lucide-react'
import { getStatus } from '../api/client'

interface Status {
  running: boolean
  total: number
  done: number
  errors: number
  current: string
}

export default function ProcessingStatus() {
  const [status, setStatus] = useState<Status | null>(null)

  useEffect(() => {
    const poll = async () => {
      try {
        const s = await getStatus()
        setStatus(s)
      } catch (error) {
        console.error('Failed to poll processing status', error)
      }
    }

    void poll()
    const interval = setInterval(poll, 2000)
    return () => clearInterval(interval)
  }, [])

  if (!status?.running) return null

  const pct = status.total > 0 ? Math.round((status.done / status.total) * 100) : 0

  return (
    <div className="fixed bottom-5 right-5 bg-slate-900/95 backdrop-blur-md border border-slate-700/60 rounded-2xl p-4 w-80 shadow-2xl z-50 fade-in">
      <div className="flex items-center gap-2.5 mb-3">
        <Loader2 size={15} className="text-indigo-400 animate-spin" />
        <span className="text-sm text-slate-100 font-medium">正在处理论文</span>
        <span className="ml-auto text-sm font-mono tabular-nums text-indigo-300">{pct}%</span>
      </div>
      <div className="w-full bg-slate-800 rounded-full h-1.5 mb-2.5 overflow-hidden">
        <div
          className="bg-gradient-to-r from-indigo-500 to-indigo-400 h-full rounded-full transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="flex items-center gap-2 text-xs text-slate-400">
        <span className="tabular-nums font-mono">{status.done}/{status.total}</span>
        {status.current && (
          <span className="line-clamp-2 text-slate-500 text-safe-wrap">· {status.current}</span>
        )}
      </div>
      {status.errors > 0 && (
        <p className="text-xs text-red-400 mt-2 flex items-center gap-1.5">
          <AlertCircle size={11} />
          {status.errors} 个失败
        </p>
      )}
    </div>
  )
}
