import { useEffect, useState } from 'react'
import { X, FileText, Tag, Link as LinkIcon, Clock, Pencil, Trash2 } from 'lucide-react'
import { getNode, firstPageUrl, type NodeDetail as NodeDetailType, type GraphNode } from '../api/client'

const TYPE_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  paper: { bg: 'bg-indigo-500/15', text: 'text-indigo-300', label: '论文' },
  technique: { bg: 'bg-emerald-500/15', text: 'text-emerald-300', label: '技术' },
  dataset: { bg: 'bg-amber-500/15', text: 'text-amber-300', label: '数据集' },
  problem_area: { bg: 'bg-cyan-500/15', text: 'text-cyan-300', label: '研究领域' },
  finding: { bg: 'bg-purple-500/15', text: 'text-purple-300', label: '发现' },
  concept: { bg: 'bg-teal-500/15', text: 'text-teal-300', label: '概念' },
  entity: { bg: 'bg-pink-500/15', text: 'text-pink-300', label: '实体' },
  topic: { bg: 'bg-indigo-500/15', text: 'text-indigo-300', label: '主题' },
  fact: { bg: 'bg-amber-500/15', text: 'text-amber-300', label: '事实' },
}

const RELATION_LABELS: Record<string, string> = {
  uses: '使用',
  builds_on: '基于',
  trained_on: '训练于',
  evaluated_on: '评测于',
  compared_to: '对比',
  similar: '相似',
  finding: '发现',
  belongs_to: '属于',
  curated_link: '人工关联',
}

interface Props {
  node: GraphNode | null
  onClose: () => void
  onNavigate: (nodeId: string) => void
  onEditManualConcept: (node: GraphNode) => void
  onSuppressNode: (node: GraphNode) => void
  busyNodeId?: string | null
}

export default function NodeDetail({
  node,
  onClose,
  onNavigate,
  onEditManualConcept,
  onSuppressNode,
  busyNodeId,
}: Props) {
  const [detail, setDetail] = useState<NodeDetailType | null>(null)
  const nodeId = node?.id

  useEffect(() => {
    if (!nodeId) return

    let cancelled = false
    const loadDetail = async () => {
      try {
        const result = await getNode(parseInt(nodeId))
        if (!cancelled) setDetail(result)
      } catch (error) {
        console.error('Failed to load node detail', error)
        if (!cancelled) setDetail(null)
      }
    }

    void loadDetail()
    return () => { cancelled = true }
  }, [nodeId])

  if (!node) return null

  const visibleDetail = String(detail?.id) === node.id ? detail : null
  const style = TYPE_STYLES[node.node_type] || { bg: 'bg-slate-700', text: 'text-slate-300', label: node.node_type }

  return (
    <aside className="w-[26rem] max-w-[42vw] min-w-[22rem] h-full bg-[#0f1117] border-l border-slate-800/80 flex flex-col overflow-hidden fade-in shrink-0">
      {/* Header */}
      <div className="px-5 py-4 border-b border-slate-800/80">
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex flex-wrap items-center gap-2 mb-2">
              <span className={`chip ${style.bg} ${style.text} text-xs`}>
                {style.label}
              </span>
              <span className={`chip text-xs ${node.origin === 'manual' ? 'bg-teal-500/10 text-teal-200 border border-teal-500/30' : 'bg-slate-800 text-slate-400 border border-slate-700/60'}`}>
                {node.origin === 'manual' ? '手动策展' : '自动抽取'}
              </span>
              {visibleDetail?.connected_nodes && (
                <span className="text-xs text-slate-500">
                  {visibleDetail.connected_nodes.length} 个关联
                </span>
              )}
            </div>
            <h3 className="text-lg font-semibold text-white leading-snug tracking-tight text-safe-wrap">
              {node.title}
            </h3>
            <p className="text-sm text-slate-500 mt-2">
              查看节点简介、来源论文与关联关系。
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-slate-500 hover:text-white p-1.5 -mr-1 shrink-0 rounded-lg hover:bg-slate-800/60 transition-colors"
          >
            <X size={16} />
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto p-5 space-y-6">
        {/* Content */}
        {node.content && (
          <section className="surface-card p-4">
            <div className="section-label mb-2">节点概述</div>
            <p className="prose-reading whitespace-pre-wrap text-safe-wrap">{node.content}</p>
          </section>
        )}

        {(visibleDetail?.can_edit || visibleDetail?.can_hide) && (
          <section className="surface-card p-4">
            <div className="section-label mb-3">策展操作</div>
            <div className="flex flex-wrap gap-2">
              {visibleDetail?.can_edit && (
                <button
                  onClick={() => onEditManualConcept(node)}
                  disabled={busyNodeId === node.id}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-slate-700 bg-slate-900/70 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800 disabled:opacity-50"
                >
                  <Pencil size={13} />
                  编辑概念
                </button>
              )}
              {visibleDetail?.can_hide && (
                <button
                  onClick={() => onSuppressNode(node)}
                  disabled={busyNodeId === node.id}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-200 hover:bg-amber-500/15 disabled:opacity-50"
                >
                  <Trash2 size={13} />
                  从概念层移除
                </button>
              )}
            </div>
            <p className="mt-3 text-xs leading-relaxed text-slate-500">
              这里的“删除”是隐藏该概念节点，不会改动原始论文；下次重处理也不会把你的人工概念清空。
            </p>
          </section>
        )}

        {/* Created at */}
        {node.created_at && (
          <p className="text-xs text-slate-500 flex items-center gap-1.5">
            <Clock size={11} />
            创建于 {new Date(node.created_at).toLocaleString()}
          </p>
        )}

        {/* Aliases / tags */}
        {node.tags && node.tags.length > 0 && (
          <section>
            <div className="flex items-center gap-1.5 mb-2.5 text-slate-500">
              <Tag size={11} />
              <span className="section-label">别名 / 标签</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {node.tags.map(tag => (
                <span
                  key={tag}
                  className="chip bg-slate-800/80 text-slate-300 border border-slate-700/40 text-xs"
                >
                  {tag}
                </span>
              ))}
            </div>
          </section>
        )}

        {/* Source papers */}
        {node.source_paper_ids && node.source_paper_ids.length > 0 && (
          <section>
            <div className="flex items-center gap-1.5 mb-2.5 text-slate-500">
              <FileText size={11} />
              <span className="section-label">来源论文 · {node.source_paper_ids.length}</span>
            </div>
            {visibleDetail?.linked_papers && visibleDetail.linked_papers.length > 0 && (
              <div className="space-y-1.5 mb-3">
                {visibleDetail.linked_papers.map(paper => (
                  <div
                    key={paper.id}
                    className="rounded-xl border border-slate-800 bg-slate-900/60 px-3 py-2"
                  >
                    <p className="text-sm text-slate-200 leading-snug text-safe-wrap">{paper.title}</p>
                    <p className="mt-1 text-[11px] text-slate-500">
                      paper #{paper.id} · {paper.processed ? '已处理' : '未处理'}
                    </p>
                  </div>
                ))}
              </div>
            )}
            <div className="grid grid-cols-2 gap-2.5">
              {node.source_paper_ids.slice(0, 9).map(pid => (
                <img
                  key={pid}
                  src={firstPageUrl(pid)}
                  alt={`paper-${pid}`}
                  className="w-full aspect-[3/4] object-cover rounded-lg border border-slate-800 hover:border-indigo-500/60 transition-colors"
                  onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                />
              ))}
            </div>
          </section>
        )}

        {/* Connected nodes */}
        {visibleDetail?.connected_nodes && visibleDetail.connected_nodes.length > 0 && (
          <section>
            <div className="flex items-center gap-1.5 mb-2.5 text-slate-500">
              <LinkIcon size={11} />
              <span className="section-label">关联节点 · {visibleDetail.connected_nodes.length}</span>
            </div>
            <div className="space-y-1.5">
              {visibleDetail.connected_nodes.slice(0, 20).map(cn => {
                const edge = visibleDetail.edges.find(
                  e => e.source === cn.id || e.target === cn.id
                )
                const rel = edge?.relation_type
                const cnStyle = TYPE_STYLES[cn.node_type] || { bg: 'bg-slate-700', text: 'text-slate-400', label: cn.node_type }
                return (
                  <button
                    key={cn.id}
                    onClick={() => onNavigate(String(cn.id))}
                    className="w-full text-left px-3.5 py-3 bg-slate-900/60 hover:bg-slate-800 border border-slate-800 hover:border-slate-700 rounded-xl transition-colors group"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm text-slate-200 group-hover:text-white leading-snug text-safe-wrap">
                        {cn.title}
                      </span>
                      <span className={`chip ${cnStyle.bg} ${cnStyle.text} text-[10px] shrink-0`}>
                        {cnStyle.label}
                      </span>
                    </div>
                    {rel && rel !== 'similar' && (
                      <p className="text-xs text-slate-500 mt-1.5">
                        {RELATION_LABELS[rel] || rel}
                      </p>
                    )}
                  </button>
                )
              })}
            </div>
          </section>
        )}
      </div>
    </aside>
  )
}
