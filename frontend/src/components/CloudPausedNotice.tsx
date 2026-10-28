import { CloudOff } from 'lucide-react'
import { t as tr } from '../i18n/catalog'
import { useLocale } from '../i18n/preferences'
import { CLOUD_PAUSED_MESSAGE, CLOUD_PAUSED_TITLE } from '../lib/cloudPolicy'

export default function CloudPausedNotice() {
  useLocale()
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-950/40 p-4" aria-label={tr(CLOUD_PAUSED_TITLE)}>
      <div className="flex items-start gap-2">
        <CloudOff size={16} className="mt-0.5 shrink-0 text-slate-400" />
        <div>
          <p className="text-sm font-medium text-slate-200">{tr(CLOUD_PAUSED_TITLE)}</p>
          <p className="mt-1 text-xs leading-relaxed text-slate-400">{tr(CLOUD_PAUSED_MESSAGE)}</p>
        </div>
      </div>
    </section>
  )
}
