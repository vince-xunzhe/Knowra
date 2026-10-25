import { t as tr } from '../i18n/catalog'
import { useLocale } from '../i18n/preferences'
import { CheckCircle2, Clock, Loader2, XCircle } from 'lucide-react'
import type { PaperRecord } from '../api/client'
import { inferPaperProcessMeta, type ProcessStatusHint } from './paperProcessMeta'

export default function PaperProcessBadge({
  paper,
  status,
  pending,
  large,
}: {
  paper: PaperRecord
  status?: ProcessStatusHint | null
  pending?: boolean
  large?: boolean
}) {
  useLocale()
  const meta = inferPaperProcessMeta(paper, status, pending)
  const iconSize = large ? 14 : 11
  const cls = large ? 'text-xs px-2 py-0.5' : 'text-[11px] px-1.5 py-0'
  if (meta.stage === 'running') {
    return (
      <span className={`chip bg-indigo-500/15 text-indigo-300 ${cls}`}>
        <Loader2 size={iconSize} className="animate-spin" /> {tr("处理中")}</span>
    )
  }
  if (meta.stage === 'processed') {
    return (
      <span className={`chip bg-emerald-500/15 text-emerald-300 ${cls}`}>
        <CheckCircle2 size={iconSize} /> {tr("已处理")}</span>
    )
  }
  if (meta.stage === 'failed') {
    return (
      <span className={`chip bg-red-500/15 text-red-300 ${cls}`}>
        <XCircle size={iconSize} /> {tr("失败")}</span>
    )
  }
  return (
    <span className={`chip bg-slate-700/40 text-slate-400 ${cls}`}>
      <Clock size={iconSize} /> {tr("待处理")}</span>
  )
}
