import { t as tr } from '../i18n/catalog'
import { useLocale } from '../i18n/preferences'
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from 'react'
import { AlertTriangle, ExternalLink, X } from 'lucide-react'
import { getPaperExtractRoute, type PaperExtractRoute } from '../api/client'
import {
  confirmationForRoute,
  decidePaperExtractRoute,
  type FileSearchConfirmDecision,
} from '../api/paperExtractConfirm'

type FileSearchConfirmationContextValue = {
  confirmIfNeeded: () => Promise<FileSearchConfirmDecision>
}

const FileSearchConfirmationContext = createContext<FileSearchConfirmationContextValue | null>(null)

async function inspectPaperExtractRoute() {
  return decidePaperExtractRoute(await getPaperExtractRoute())
}


export function FileSearchConfirmationProvider({ children }: { children: ReactNode }) {
  useLocale()
  const [route, setRoute] = useState<PaperExtractRoute | null>(null)
  const resolverRef = useRef<((decision: FileSearchConfirmDecision) => void) | null>(null)

  const close = useCallback((decision: FileSearchConfirmDecision) => {
    const resolve = resolverRef.current
    resolverRef.current = null
    setRoute(null)
    resolve?.(decision)
  }, [])

  const confirmIfNeeded = useCallback(async () => {
    const inspected = await inspectPaperExtractRoute()
    if (inspected.status !== 'needs_confirmation') {
      return inspected
    }
    return await new Promise<FileSearchConfirmDecision>(resolve => {
      resolverRef.current?.( { status: 'cancelled' } )
      resolverRef.current = resolve
      setRoute(inspected.route)
    })
  }, [])

  const value = useMemo(() => ({ confirmIfNeeded }), [confirmIfNeeded])

  return (
    <FileSearchConfirmationContext.Provider value={value}>
      {children}
      {route ? (
        <FileSearchConfirmationModal
          route={route}
          onCancel={() => close({ status: 'cancelled' })}
          onContinue={() => close({
            status: 'confirmed',
            confirmation: confirmationForRoute(route),
          })}
        />
      ) : null}
    </FileSearchConfirmationContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useFileSearchConfirmation() {
  const context = useContext(FileSearchConfirmationContext)
  if (!context) {
    throw new Error('useFileSearchConfirmation must be used within FileSearchConfirmationProvider')
  }
  return context
}


function FileSearchConfirmationModal({
  route,
  onCancel,
  onContinue,
}: {
  route: PaperExtractRoute
  onCancel: () => void
  onContinue: () => void
}) {
  useLocale()
  const modelLabel = route.model_label || route.model_id || route.upstream_model
  const providerLabel = route.provider_label || route.provider_id || 'OpenAI'
  const apiLabel = route.file_search_api === 'responses' ? 'Responses + file_search' : 'Assistants + file_search'

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/65 p-5 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="file-search-confirm-title"
      onMouseDown={event => {
        if (event.target === event.currentTarget) onCancel()
      }}
    >
      <div className="flex w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-amber-500/30 bg-[var(--surface-0d1119)] shadow-2xl shadow-black/50">
        <header className="flex items-start gap-3 border-b border-slate-800 px-5 py-4">
          <span className="mt-0.5 rounded-lg border border-amber-400/25 bg-amber-400/10 p-2 text-amber-300">
            <AlertTriangle size={17} />
          </span>
          <div className="min-w-0 flex-1">
            <h2 id="file-search-confirm-title" className="text-sm font-semibold text-slate-100">
              {tr("将通过 OpenAI API 进行论文抽取")}
            </h2>
            <p className="mt-1 text-xs leading-relaxed text-slate-400">
              {tr("{0} · {1} · {2}", { 0: providerLabel, 1: modelLabel, 2: apiLabel })}
            </p>
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="rounded-md p-1 text-slate-500 hover:bg-slate-800 hover:text-slate-200"
            aria-label={tr("取消")}
          >
            <X size={16} />
          </button>
        </header>
        <div className="space-y-3 px-5 py-4 text-sm leading-relaxed text-slate-300">
          <p>{tr("当前论文抽取绑定的是 OpenAI API 的 file_search，按量付费，费用不包含在 ChatGPT 或 Codex 订阅额度中。")}</p>
          <p>{tr("继续后，系统可能上传 PDF 到该 API 提供方，并产生模型 token、检索调用和向量存储等费用。")}</p>
          <p>{tr("具体价格请查看 OpenAI 官方定价（会随官方更新）。")}</p>
          <div className="flex flex-wrap gap-3 text-xs">
            <a
              href={route.pricing_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-amber-300 hover:underline"
            >
              {tr("查看官方定价")}
              <ExternalLink size={12} />
            </a>
            <a
              href={route.file_search_guide_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-slate-400 hover:underline"
            >
              {tr("查看 file_search 说明")}
              <ExternalLink size={12} />
            </a>
          </div>
        </div>
        <footer className="flex justify-end gap-2 border-t border-slate-800 px-5 py-3">
          <button
            type="button"
            onClick={onCancel}
            className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800"
          >
            {tr("取消")}
          </button>
          <button
            type="button"
            onClick={onContinue}
            className="rounded-lg bg-amber-500 px-3 py-1.5 text-sm font-medium text-slate-950 hover:bg-amber-400"
          >
            {tr("继续抽取")}
          </button>
        </footer>
      </div>
    </div>
  )
}
