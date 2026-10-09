import type { GraphData, GraphEdge, GraphNode, PaperRecord } from '../api/client'

export type ExplorerMode = 'overview' | 'local' | 'path'
export interface Topic { id: string; label: string | null; nodes: GraphNode[] }
export interface ViewNode { id: string; title: string; type: string; topic: string; status?: string; count?: number; x: number; y: number }
export interface ViewEdge { id: string; source: string; target: string; relation: string; count?: number; original?: GraphEdge }
export interface ExplorerGraph { nodes: ViewNode[]; edges: ViewEdge[] }
export const UNGROUPED = 'uncategorized:'
export const topicId = (node: GraphNode) => node.category?.trim() ? `category:${node.category.trim()}` : UNGROUPED

/** Older graph responses omit category. Resolve it from the already loaded
 * paper catalog and source IDs, without classifying or writing anything. */
export function withPaperCategories(data: GraphData, papers: Pick<PaperRecord, 'id' | 'paper_category'>[]): GraphData {
  const categories = new Map(papers.map(p => [String(p.id), p.paper_category?.trim()]))
  return { ...data, nodes: data.nodes.map(node => {
    if (node.category?.trim()) return node
    const votes = new Map<string, number>()
    const sources = new Set([...(node.source_paper_ids || []), ...(node.paper_id == null ? [] : [node.paper_id])].map(String))
    for (const id of sources) {
      const category = categories.get(id)
      if (category) votes.set(category, (votes.get(category) || 0) + 1)
    }
    const category = [...votes].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0]?.[0]
    return category ? { ...node, category } : node
  }) }
}
// Presentation grouping only: categories are supplied by the graph API. This
// deliberately does not claim to be community detection or alter stored edges.
export function groupTopics(data: GraphData): Topic[] {
  const topics = new Map<string, Topic>()
  for (const node of data.nodes) {
    const id = topicId(node)
    if (!topics.has(id)) topics.set(id, { id, label: node.category?.trim() || null, nodes: [] })
    topics.get(id)!.nodes.push(node)
  }
  return [...topics.values()].sort((a, b) => a.id.localeCompare(b.id)).map(t => ({ ...t, nodes: t.nodes.slice().sort(byTitle) }))
}
export function byTitle(a: GraphNode, b: GraphNode) { return a.title.localeCompare(b.title) || a.id.localeCompare(b.id) }
export function visibleRelations(data: GraphData, similar: boolean): GraphData {
  const ids = new Set(data.nodes.map(n => n.id))
  return { nodes: data.nodes, edges: data.edges.filter(e => ids.has(e.source) && ids.has(e.target) && (similar || e.relation_type !== 'similar')) }
}
export function topicOverview(topics: Topic[], data: GraphData, ungroupedLabel: string): ExplorerGraph {
  const membership = new Map(topics.flatMap(t => t.nodes.map(n => [n.id, t.id] as const)))
  const links = new Map<string, ViewEdge>()
  for (const e of data.edges) {
    const a = membership.get(e.source), b = membership.get(e.target)
    if (!a || !b || a === b) continue
    const [source, target] = [a, b].sort()
    const relation = e.relation_type === 'similar' ? 'similar' : 'aggregate'
    const key = JSON.stringify([source, target, relation])
    const link = links.get(key)
    if (link) link.count! += 1
    else links.set(key, { id: `topic-edge:${key}`, source, target, relation, count: 1 })
  }
  return {
    nodes: topics.map((t, i) => ({ id: t.id, title: t.label || ungroupedLabel, type: 'group', topic: t.id, count: t.nodes.length, x: topics.length === 1 ? 0 : Math.cos(i / topics.length * Math.PI * 2 - Math.PI / 2) * Math.max(220, topics.length * 40), y: topics.length === 1 ? 0 : Math.sin(i / topics.length * Math.PI * 2 - Math.PI / 2) * Math.max(160, topics.length * 30) })),
    edges: [...links.values()],
  }
}
function adjacency(data: GraphData) {
  const index = new Map(data.nodes.map(n => [n.id, [] as { id: string; edge: GraphEdge }[]]))
  // Stable tie-breaking favors explicit relationships over similarity edges.
  for (const edge of [...data.edges].sort((a, b) => Number(a.relation_type === 'similar') - Number(b.relation_type === 'similar') || a.id.localeCompare(b.id))) {
    if (!index.has(edge.source) || !index.has(edge.target)) continue
    index.get(edge.source)!.push({ id: edge.target, edge })
    index.get(edge.target)!.push({ id: edge.source, edge })
  }
  return index
}
export function neighborhood(data: GraphData, rootId: string, depth: number, limit = 80) {
  const index = adjacency(data)
  if (!index.has(rootId)) return { data: { nodes: [], edges: [] } as GraphData, total: 0 }
  const visited = new Set([rootId]), queue = [{ id: rootId, depth: 0 }]
  for (let i = 0; i < queue.length; i++) {
    const entry = queue[i]
    if (entry.depth >= depth) continue
    for (const n of index.get(entry.id) || []) {
      if (!visited.has(n.id)) { visited.add(n.id); queue.push({ id: n.id, depth: entry.depth + 1 }) }
    }
  }
  const ids = new Set(queue.slice(0, limit).map(n => n.id))
  return { data: { nodes: data.nodes.filter(n => ids.has(n.id)), edges: data.edges.filter(e => ids.has(e.source) && ids.has(e.target)) }, total: visited.size }
}
export function topicSubgraph(data: GraphData, topic: Topic, limit = 80) {
  const ids = new Set(topic.nodes.slice(0, limit).map(n => n.id))
  return { data: { nodes: data.nodes.filter(n => ids.has(n.id)), edges: data.edges.filter(e => ids.has(e.source) && ids.has(e.target)) }, total: topic.nodes.length }
}
// Undirected connectivity within the user's current filters. Original edge
// directions and provenance remain intact in the result and evidence panel.
export function shortestConnection(data: GraphData, source: string, target: string, maxDepth = 6): { data: GraphData; order: string[]; found: boolean } {
  const empty = { data: { nodes: [], edges: [] }, order: [], found: false }
  const index = adjacency(data)
  if (!index.has(source) || !index.has(target)) return empty
  const seen = new Map<string, { previous: string; edge: GraphEdge }>()
  const visited = new Set([source]), queue = [{ id: source, depth: 0 }]
  for (let i = 0; i < queue.length && !visited.has(target); i++) {
    const item = queue[i]
    if (item.depth >= maxDepth) continue
    for (const next of index.get(item.id) || []) {
      if (visited.has(next.id)) continue
      visited.add(next.id); seen.set(next.id, { previous: item.id, edge: next.edge }); queue.push({ id: next.id, depth: item.depth + 1 })
      if (next.id === target) break
    }
  }
  if (!visited.has(target)) return empty
  const order = [target], edges: GraphEdge[] = []
  let cursor = target
  while (cursor !== source) { const step = seen.get(cursor)!; edges.unshift(step.edge); cursor = step.previous; order.unshift(cursor) }
  const ids = new Set(order)
  return { data: { nodes: data.nodes.filter(n => ids.has(n.id)), edges }, order, found: true }
}
export function detailGraph(data: GraphData, rootId?: string, pathOrder?: string[]): ExplorerGraph {
  const sorted = [...data.nodes].sort(byTitle)
  const ordered = pathOrder ? pathOrder.map(id => sorted.find(n => n.id === id)!).filter(Boolean) : [...sorted.filter(n => n.id === rootId), ...sorted.filter(n => n.id !== rootId)]
  const root = ordered.find(n => n.id === rootId)
  return {
    nodes: ordered.map((n, i) => {
      let x: number, y: number
      if (pathOrder) { x = i * 245; y = 0 }
      else if (root && i === 0) { x = 0; y = 0 }
      else {
        const index = root ? i - 1 : i
        const ring = Math.floor(index / 12) + 1
        const count = Math.min(12, ordered.length - (root ? 1 : 0) - (ring - 1) * 12)
        const angle = (index % 12) / count * Math.PI * 2 - Math.PI / 2
        x = Math.cos(angle) * ring * 290; y = Math.sin(angle) * ring * 220
      }
      return { id: n.id, title: n.title, type: n.node_type, topic: topicId(n), status: n.promotion_status, x, y }
    }),
    edges: data.edges.map(e => ({ id: e.id, source: e.source, target: e.target, relation: e.relation_type, original: e })),
  }
}
export const relationLabels: Record<string, string> = { uses: '使用', builds_on: '基于', trained_on: '训练于', evaluated_on: '评测于', compared_to: '对比', similar: '相似', related: '相关', contrasts_with: '对照', belongs_to: '属于', curated_link: '人工关联' }
export const typeLabels: Record<string, string> = { paper: '论文', technique: '技术', dataset: '数据集', concept: '概念', topic: '主题', entity: '实体', fact: '事实' }
export const originLabels: Record<string, string> = { explicit: '论文抽取', inferred: '模型推断', embedding: '向量相似度', manual: '人工创建', legacy: '历史数据' }
export function topicColor(id: string) {
  let hash = 0
  for (const c of id) hash = (Math.imul(hash, 31) + c.charCodeAt(0)) | 0
  return id === UNGROUPED ? '#8190a4' : `hsl(${Math.abs(hash) % 360}, 40%, 62%)`
}
