import { AlertTriangle, CheckCircle2, Info, Loader2, RefreshCw, XCircle } from 'lucide-react'

export type TaskNoticeTone = 'info' | 'success' | 'warning' | 'error'

interface TaskNoticeProps {
  tone: TaskNoticeTone
  title: string
  detail?: string | null
  busy?: boolean
  onRetry?: () => void
  retryLabel?: string
  className?: string
}

const TONE_CLASS: Record<TaskNoticeTone, string> = {
  info: 'border-indigo-500/35 bg-indigo-500/10 text-indigo-100',
  success: 'border-emerald-500/35 bg-emerald-500/10 text-emerald-100',
  warning: 'border-amber-500/35 bg-amber-500/10 text-amber-100',
  error: 'border-rose-500/35 bg-rose-500/10 text-rose-100',
}

function ToneIcon({ tone, busy }: { tone: TaskNoticeTone; busy?: boolean }) {
  if (busy) return <Loader2 size={14} className="animate-spin" />
  if (tone === 'success') return <CheckCircle2 size={14} />
  if (tone === 'warning') return <AlertTriangle size={14} />
  if (tone === 'error') return <XCircle size={14} />
  return <Info size={14} />
}

export default function TaskNotice({
  tone,
  title,
  detail,
  busy,
  onRetry,
  retryLabel = '重试',
  className = '',
}: TaskNoticeProps) {
  return (
    <div className={`rounded-xl border px-3 py-2 ${TONE_CLASS[tone]} ${className}`}>
      <div className="flex items-start gap-2">
        <span className="mt-0.5 shrink-0">
          <ToneIcon tone={tone} busy={busy} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-xs font-medium leading-relaxed text-safe-wrap">{title}</p>
          {detail && (
            <p className="mt-0.5 text-[11px] leading-relaxed opacity-90 text-safe-wrap">{detail}</p>
          )}
        </div>
        {onRetry && !busy && (
          <button
            onClick={onRetry}
            className="inline-flex shrink-0 items-center gap-1 rounded-md border border-current/35 bg-black/10 px-2 py-1 text-[10.5px] hover:bg-black/20"
          >
            <RefreshCw size={11} />
            {retryLabel}
          </button>
        )}
      </div>
    </div>
  )
}
