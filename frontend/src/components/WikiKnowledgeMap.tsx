import { useMemo } from 'react'
import { Clock3, Link2, Loader2, Plus, X } from 'lucide-react'
import type { WikiGraphData, WikiGraphNode } from '../api/client'


const COLUMN_WIDTH_REM = 15.5
const TYPE_SECTION_ORDER = ['concept', 'problem_area', 'technique', 'dataset']

const TYPE_LABELS: Record<string, string> = {
  concept: '概念',
  problem_area: '研究领域',
  technique: '技术',
  dataset: '数据集',
}

const TYPE_STYLES: Record<string, string> = {
  concept: 'border-teal-500/30 bg-teal-500/10 text-teal-100',
  problem_area: 'border-cyan-500/30 bg-cyan-500/10 text-cyan-100',
  technique: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-100',
  dataset: 'border-amber-500/30 bg-amber-500/10 text-amber-100',
}

type LaneNode = {
  node: WikiGraphNode
  linkedPaperIds: number[]
  avgIndex: number
}

type Lane = {
  name: string
  papers: WikiGraphNode[]
  nodesByType: Record<string, LaneNode[]>
  paperNodeCounts: Record<number, number>
}


function relTime(iso?: string | null) {
  if (!iso) return '未编译'
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return '未知'
  const diff = Date.now() - t
  const sec = Math.floor(diff / 1000)
  if (sec < 60) return `${sec} 秒前`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min} 分钟前`
  const hr = Math.floor(min / 60)
  if (hr < 48) return `${hr} 小时前`
  const day = Math.floor(hr / 24)
  return `${day} 天前`
}


function laneData(graph: WikiGraphData): { lanes: Lane[]; maxColumns: number } {
  const categoryOrder = graph.categories.map(category => category.name)
  const paperNodes = graph.nodes.filter(node => node.kind === 'paper')
  const conceptNodes = graph.nodes.filter(node => node.kind === 'concept')
  const supportEdges = graph.edges.filter(edge => edge.relation_type === 'supports')

  const papersByCategory = new Map<string, WikiGraphNode[]>()
  for (const category of categoryOrder) papersByCategory.set(category, [])
  for (const paper of paperNodes) {
    const category = paper.category || '其他'
    if (!papersByCategory.has(category)) papersByCategory.set(category, [])
    papersByCategory.get(category)!.push(paper)
  }

  let maxColumns = 0
  const lanes: Lane[] = []

  for (const category of papersByCategory.keys()) {
    const papers = (papersByCategory.get(category) || []).sort((a, b) => {
      if ((a.year || 0) !== (b.year || 0)) return (a.year || 0) - (b.year || 0)
      return a.x - b.x
    })
    if (papers.length === 0) continue

    maxColumns = Math.max(maxColumns, papers.length)
    const indexByPaperId = new Map<number, number>()
    for (const [index, paper] of papers.entries()) {
      if (paper.paper_id != null) indexByPaperId.set(paper.paper_id, index)
    }

    const nodesByType: Record<string, LaneNode[]> = {}
    const paperNodeCounts: Record<number, number> = {}
    for (const nodeType of TYPE_SECTION_ORDER) nodesByType[nodeType] = []

    for (const node of conceptNodes.filter(item => item.category === category)) {
      const linkedPaperIds = supportEdges
        .filter(edge => edge.source === node.id)
        .map(edge => Number(edge.target.replace('paper:', '')))
        .filter(pid => indexByPaperId.has(pid))
      if (linkedPaperIds.length === 0) continue

      for (const pid of linkedPaperIds) {
        paperNodeCounts[pid] = (paperNodeCounts[pid] || 0) + 1
      }

      const avgIndex = linkedPaperIds.reduce((sum, pid) => sum + (indexByPaperId.get(pid) || 0), 0) / linkedPaperIds.length
      const bucket = nodesByType[node.node_type || 'concept'] || (nodesByType[node.node_type || 'concept'] = [])
      bucket.push({ node, linkedPaperIds, avgIndex })
    }

    for (const bucket of Object.values(nodesByType)) {
      bucket.sort((a, b) => {
        if (a.avgIndex !== b.avgIndex) return a.avgIndex - b.avgIndex
        return a.node.title.localeCompare(b.node.title)
      })
    }

    lanes.push({ name: category, papers, nodesByType, paperNodeCounts })
  }

  return { lanes, maxColumns }
}


export default function WikiKnowledgeMap({
  data,
  selectedId,
  onPick,
  onSuppressNode,
  suppressingNodeId,
  onCreateConcept,
}: {
  data: WikiGraphData
  selectedId: string | null
  onPick: (node: WikiGraphNode) => void
  onSuppressNode?: (node: WikiGraphNode) => void
  suppressingNodeId?: string | null
  onCreateConcept?: (category: string, paperIds: number[]) => void
}) {
  const { lanes, maxColumns } = useMemo(() => laneData(data), [data])
  const minWidth = `${Math.max(3, maxColumns) * COLUMN_WIDTH_REM}rem`

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex-1 overflow-auto px-5 py-4">
        <div className="space-y-8" style={{ minWidth }}>
          {lanes.map(lane => (
            <LaneView
              key={lane.name}
              lane={lane}
              maxColumns={maxColumns}
              selectedId={selectedId}
              onPick={onPick}
              onSuppressNode={onSuppressNode}
              suppressingNodeId={suppressingNodeId}
              onCreateConcept={onCreateConcept}
            />
          ))}
        </div>
      </div>
      <div className="border-t border-slate-800/80 bg-slate-950/70 px-5 py-2 text-[11px] text-slate-500">
        每条 lane 对应一个论文大类；论文按年份链式排列；下方小框是挂在这条论文链上的概念、技术、数据集与研究领域。
      </div>
    </div>
  )
}


function LaneView({
  lane,
  maxColumns,
  selectedId,
  onPick,
  onSuppressNode,
  suppressingNodeId,
  onCreateConcept,
}: {
  lane: Lane
  maxColumns: number
  selectedId: string | null
  onPick: (node: WikiGraphNode) => void
  onSuppressNode?: (node: WikiGraphNode) => void
  suppressingNodeId?: string | null
  onCreateConcept?: (category: string, paperIds: number[]) => void
}) {
  const gridStyle = { gridTemplateColumns: `repeat(${maxColumns}, minmax(0, 1fr))` }
  const lanePaperIds = lane.papers.map(paper => paper.paper_id).filter((id): id is number => id != null)

  return (
    <section className="rounded-2xl border border-slate-800 bg-slate-950/35 p-4">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-slate-100">{lane.name}</h3>
          <p className="mt-1 text-xs text-slate-500">
            {lane.papers.length} 篇论文沿时间链展开
          </p>
        </div>
        {onCreateConcept && (
          <button
            onClick={() => onCreateConcept(lane.name, lanePaperIds)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-teal-500/30 bg-teal-500/10 px-3 py-1.5 text-xs text-teal-200 transition-colors hover:bg-teal-500/20"
          >
            <Plus size={12} />
            添加概念
          </button>
        )}
      </div>

      <div className="rounded-2xl border border-indigo-500/20 bg-indigo-500/5 px-3 py-4">
        <div className="grid gap-4" style={gridStyle}>
          {Array.from({ length: maxColumns }).map((_, index) => {
            const paper = lane.papers[index]
            return paper ? (
              <PaperCard
                key={paper.id}
                first={index === 0}
                paper={paper}
                selected={selectedId === paper.id}
                linkedCount={lane.paperNodeCounts[paper.paper_id || 0] || 0}
                onPick={onPick}
              />
            ) : (
              <div key={`empty-${index}`} />
            )
          })}
        </div>
      </div>

      <div className="mt-4 space-y-3">
        {TYPE_SECTION_ORDER.map(nodeType => {
          const nodes = lane.nodesByType[nodeType] || []
          if (nodes.length === 0) return null
          return (
            <LaneNodeSection
              key={`${lane.name}-${nodeType}`}
              title={TYPE_LABELS[nodeType] || nodeType}
              nodes={nodes}
              selectedId={selectedId}
              onPick={onPick}
              onSuppressNode={onSuppressNode}
              suppressingNodeId={suppressingNodeId}
            />
          )
        })}
      </div>
    </section>
  )
}


function LaneNodeSection({
  title,
  nodes,
  selectedId,
  onPick,
  onSuppressNode,
  suppressingNodeId,
}: {
  title: string
  nodes: LaneNode[]
  selectedId: string | null
  onPick: (node: WikiGraphNode) => void
  onSuppressNode?: (node: WikiGraphNode) => void
  suppressingNodeId?: string | null
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-center gap-2">
        <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">
          {title}
        </span>
        <span className="text-[11px] text-slate-600">{nodes.length}</span>
      </div>
      <div className="flex flex-wrap gap-1">
        {nodes.map(item => (
          <NodePill
            key={item.node.id}
            item={item}
            selected={selectedId === item.node.id}
            onPick={onPick}
            onSuppressNode={onSuppressNode}
            suppressing={suppressingNodeId === item.node.id}
          />
        ))}
      </div>
    </div>
  )
}


function NodePill({
  item,
  selected,
  onPick,
  onSuppressNode,
  suppressing,
}: {
  item: LaneNode
  selected: boolean
  onPick: (node: WikiGraphNode) => void
  onSuppressNode?: (node: WikiGraphNode) => void
  suppressing?: boolean
}) {
  const style = TYPE_STYLES[item.node.node_type || 'concept'] || 'border-slate-700 bg-slate-900/60 text-slate-200'

  return (
    <div
      className={`group flex items-start gap-1 rounded-md border px-2 py-1 ${
        selected ? 'border-white/70 bg-slate-800 text-white' : style
      }`}
    >
      <button
        onClick={() => onPick(item.node)}
        className="min-w-0 flex-1 text-left"
      >
        <div className="truncate text-[12px] font-medium leading-[1.1rem]">{item.node.title}</div>
        <div className="mt-0.5 flex items-center gap-1 text-[9.5px] text-slate-500">
          <Link2 size={9} />
          <span>关联 {item.linkedPaperIds.length} 篇</span>
        </div>
      </button>
      {onSuppressNode && (
        <button
          onClick={() => onSuppressNode(item.node)}
          disabled={suppressing}
          className="mt-0.5 shrink-0 rounded p-0.5 text-slate-500 transition-colors hover:bg-slate-900/40 hover:text-slate-200 disabled:opacity-50"
          title="隐藏这个概念"
        >
          {suppressing ? <Loader2 size={10} className="animate-spin" /> : <X size={10} />}
        </button>
      )}
    </div>
  )
}


function PaperCard({
  first,
  paper,
  selected,
  linkedCount,
  onPick,
}: {
  first: boolean
  paper: WikiGraphNode
  selected: boolean
  linkedCount: number
  onPick: (node: WikiGraphNode) => void
}) {
  return (
    <div className="relative">
      {!first && (
        <div className="absolute left-[-1.1rem] right-[50%] top-[2.25rem] h-[2px] bg-indigo-400/30" />
      )}
      <button
        onClick={() => onPick(paper)}
        className={`relative w-full rounded-2xl border px-4 py-3 text-left transition-colors ${
          selected
            ? 'border-white/70 bg-indigo-500/12 text-white'
            : paper.active
              ? 'border-indigo-400/50 bg-indigo-500/10 text-slate-100'
              : 'border-slate-800 bg-slate-900/75 text-slate-100 hover:border-slate-700 hover:bg-slate-900'
        }`}
      >
        <div className="mb-2 flex items-center justify-between gap-2">
          <span className="rounded-lg border border-indigo-500/30 bg-indigo-500/12 px-2 py-1 text-[10px] font-medium text-indigo-200">
            {paper.year || '年份未知'}
          </span>
          <span className="text-[10px] text-slate-500">paper #{paper.paper_id}</span>
        </div>
        <p className="line-clamp-3 text-sm font-medium leading-snug text-safe-wrap">
          {paper.title}
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
          <span className="rounded-md bg-slate-800/80 px-1.5 py-0.5">
            挂接 {linkedCount}
          </span>
        </div>
        <div className="mt-3 flex items-center gap-1.5 text-[11px] text-slate-500">
          <Clock3 size={11} />
          <span>{relTime(paper.compiled_at)}</span>
        </div>
      </button>
    </div>
  )
}
