import { useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, Hand, Bot, Sparkles, Tag } from 'lucide-react'
import type { GraphNode } from '../api/client'

interface Props {
  /** Concept-eligible nodes already filtered by candidateMode + hidden
   *  types upstream. The list view groups these by node_type. */
  nodes: GraphNode[]
  selectedId: string | null
  onPick: (node: GraphNode) => void
}

const TYPE_LABELS: Record<string, string> = {
  technique: '技术',
  dataset: '数据集',
  concept: '手动概念',
}

const TYPE_COLORS: Record<string, string> = {
  technique: '#22c55e',
  dataset: '#f59e0b',
  concept: '#14b8a6',
}

const TYPE_ORDER = ['technique', 'dataset', 'concept']

const STATUS_BADGE: Record<string, string> = {
  pending: 'bg-amber-500/15 text-amber-200 border-amber-500/30',
  promoted: 'bg-emerald-500/15 text-emerald-200 border-emerald-500/30',
  rejected: 'bg-rose-500/15 text-rose-200 border-rose-500/30',
}

/**
 * Concept catalog rendered as collapsible category sections instead of a
 * graph. Same click semantics as a graph node — selecting a row opens the
 * NodeDetail drawer the host already owns.
 */
export default function ConceptListView({ nodes, selectedId, onPick }: Props) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())

  const groups = useMemo(() => {
    const out: Record<string, GraphNode[]> = {}
    for (const n of nodes) {
      const key = TYPE_ORDER.includes(n.node_type) ? n.node_type : 'concept'
      if (!out[key]) out[key] = []
      out[key].push(n)
    }
    // Sort each group by source-paper count desc, then by title.
    for (const key of Object.keys(out)) {
      out[key].sort(
        (a, b) =>
          (b.source_paper_ids?.length ?? 0) - (a.source_paper_ids?.length ?? 0) ||
          a.title.localeCompare(b.title, 'zh-CN'),
      )
    }
    return out
  }, [nodes])

  const orderedKeys = TYPE_ORDER.filter(k => (groups[k]?.length ?? 0) > 0)

  const toggle = (key: string) =>
    setCollapsed(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  if (nodes.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-sm text-slate-500">
        暂无概念候选 — 先去「论文」页处理 PDF
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto px-6 py-5">
      <div className="max-w-4xl mx-auto space-y-5">
        {orderedKeys.map(key => {
          const list = groups[key]
          const isCollapsed = collapsed.has(key)
          return (
            <section key={key}>
              <button
                onClick={() => toggle(key)}
                className="w-full flex items-center gap-2 mb-2 text-left group"
              >
                <span className="text-slate-500 group-hover:text-slate-200 transition-colors">
                  {isCollapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
                </span>
                <span
                  className="w-2 h-2 rounded-full"
                  style={{ background: TYPE_COLORS[key] }}
                />
                <span className="text-[13px] font-semibold text-white tracking-tight">
                  {TYPE_LABELS[key] || key}
                </span>
                <span className="text-[11px] text-slate-500 tabular-nums">
                  {list.length}
                </span>
              </button>
              {!isCollapsed && (
                <ul className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                  {list.map(node => (
                    <ConceptRow
                      key={node.id}
                      node={node}
                      active={selectedId === node.id}
                      onClick={() => onPick(node)}
                    />
                  ))}
                </ul>
              )}
            </section>
          )
        })}
      </div>
    </div>
  )
}

function ConceptRow({
  node,
  active,
  onClick,
}: {
  node: GraphNode
  active: boolean
  onClick: () => void
}) {
  const status = node.promotion_status || 'promoted'
  const by = node.promoted_by
  const paperCount = node.source_paper_ids?.length ?? 0
  const tags = (node.tags || []).slice(0, 3)
  return (
    <li>
      <button
        onClick={onClick}
        className={`w-full text-left rounded-lg border px-3 py-2 transition-colors ${
          active
            ? 'border-indigo-500/60 bg-indigo-500/10'
            : 'border-slate-800 bg-slate-900/40 hover:bg-slate-800/60 hover:border-slate-700'
        }`}
      >
        <div className="flex items-start justify-between gap-2">
          <span className="text-[12.5px] text-slate-100 font-medium leading-snug text-safe-wrap line-clamp-2">
            {node.title}
          </span>
          {status !== 'promoted' && (
            <span
              className={`shrink-0 text-[9.5px] px-1 py-0 rounded border ${STATUS_BADGE[status]}`}
              title={`promotion status: ${status}`}
            >
              {status === 'pending' ? '候选' : '淘汰'}
            </span>
          )}
        </div>
        <div className="mt-1 flex items-center gap-2 text-[10.5px] text-slate-500 tabular-nums">
          <span>引用 {paperCount}</span>
          {by && (
            <>
              <span className="text-slate-700">·</span>
              <span className="inline-flex items-center gap-0.5">
                {by === 'user' ? (
                  <Hand size={9} />
                ) : by === 'llm' ? (
                  <Bot size={9} />
                ) : (
                  <Sparkles size={9} />
                )}
                {by}
              </span>
            </>
          )}
        </div>
        {tags.length > 0 && (
          <div className="mt-1.5 flex items-center gap-1 flex-wrap">
            <Tag size={9} className="text-slate-600" />
            {tags.map(t => (
              <span
                key={t}
                className="text-[9.5px] px-1 py-0 rounded bg-slate-800 text-slate-400"
              >
                {t}
              </span>
            ))}
          </div>
        )}
      </button>
    </li>
  )
}
