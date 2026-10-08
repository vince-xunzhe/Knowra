import { useState } from 'react'
import { Loader2, Search, X, LocateFixed } from 'lucide-react'
import type { GraphNode } from '../api/client'
import { t } from '../i18n/catalog'
import { useLocale } from '../i18n/preferences'

interface Props {
  nodes: GraphNode[]
  selectedId: string | null
  loading: boolean
  error: boolean
  onRetry: () => void
  onClose: () => void
  onPick: (node: GraphNode) => void
}

export default function CandidateReviewPanel({ nodes, selectedId, loading, error, onRetry, onClose, onPick }: Props) {
  useLocale()
  const [query, setQuery] = useState('')
  const needle = query.trim().toLocaleLowerCase()
  const matches = nodes.filter(node => [node.title, ...node.tags].some(text => text.toLocaleLowerCase().includes(needle)))
  const types: Record<string, string> = { technique: t('技术'), dataset: t('数据集'), concept: t('概念'), problem_area: t('研究领域') }
  return (
    <section aria-labelledby="candidate-review-title" className="shrink-0 border-b border-amber-500/25 bg-[var(--surface-0f1117)] px-4 py-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 id="candidate-review-title" className="text-sm font-semibold text-foreground">{t('待审核候选')}{!loading && !error && <span className="ml-2 text-amber-300 tabular-nums">{nodes.length}</span>}</h2>
          <p className="mt-1 text-xs text-slate-400">{t('点击候选，在节点图谱中定位并打开审核详情。')}</p>
        </div>
        <button type="button" onClick={onClose} title={t('退出候选查看')} aria-label={t('退出候选查看')} className="shrink-0 rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 focus-visible:ring-2 focus-visible:ring-indigo-400"><X size={15} /></button>
      </div>
      {loading ? (
        <p role="status" className="mt-3 flex items-center gap-2 text-xs text-slate-400"><Loader2 size={14} className="animate-spin" />{t('加载候选中…')}</p>
      ) : error ? (
        <div role="alert" className="mt-3 flex flex-wrap items-center gap-3 text-xs text-rose-300">
          {t('候选加载失败，请重试。')}
          <button type="button" onClick={onRetry} className="rounded-lg border border-slate-700 px-3 py-1.5 text-slate-200">{t('重试')}</button>
        </div>
      ) : nodes.length === 0 ? (
        <p role="status" className="mt-3 text-xs text-slate-400">{t('当前没有待审核候选。')}</p>
      ) : (
        <>
          <label className="mt-3 flex max-w-sm items-center gap-2 rounded-lg border border-slate-700 px-2.5 py-1.5 text-slate-400">
            <Search size={13} className="shrink-0" />
            <input value={query} onChange={event => setQuery(event.target.value)} placeholder={t('搜索候选名称或标签')} aria-label={t('搜索候选名称或标签')} className="min-w-0 w-full bg-transparent text-xs text-foreground outline-none" />
          </label>
          <ul className="mt-2 flex max-h-44 flex-wrap gap-2 overflow-y-auto p-0.5" aria-label={t('待审核候选')}>
            {matches.map(node => (
              <li key={node.id} className="min-w-0 w-full sm:w-auto sm:max-w-72">
                <button type="button" aria-pressed={selectedId === node.id} onClick={() => onPick(node)} className={`flex w-full items-start gap-2 rounded-lg border px-3 py-2 text-left focus-visible:ring-2 focus-visible:ring-indigo-400 ${selectedId === node.id ? 'border-indigo-400 bg-indigo-500/15' : 'border-slate-700 hover:border-amber-400/60 hover:bg-amber-500/5'}`}>
                  <LocateFixed size={14} className="mt-0.5 shrink-0 text-amber-300" />
                  <span className="min-w-0">
                    <span className="block break-words text-xs font-medium text-foreground">{node.title}</span>
                    <span className="mt-1 block text-[11px] text-slate-400">{types[node.node_type] ?? node.node_type} · {t('{0} 篇来源论文', { 0: node.source_paper_ids.length })}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {matches.length === 0 && <p role="status" className="mt-2 text-xs text-slate-400">{t('没有匹配的候选，试试其他名称或标签。')}</p>}
        </>
      )}
    </section>
  )
}
