import axios from 'axios'

export type ReviewField = 'core_contribution' | 'abstract_summary' | 'problem' | 'motivation'
export interface DomainPaper {
  id: string
  title: string
  year: string
  category: string
  sections: Record<ReviewField, string>
  eligible: boolean
  reason: 'not_reviewed' | 'empty_review' | null
  incomplete: boolean
}
export interface CanvasNode {
  id: string
  kind: 'paper' | 'text' | 'group'
  x: number
  y: number
  width: number
  height: number
  paperId: string | null
  groupId: string | null
  text: string
  locked: boolean
}
export interface CanvasEdge {
  id: string
  source: string
  target: string
  label: string
  locked: boolean
}
export interface Viewport {
  x: number
  y: number
  zoom: number
}
export interface DomainBoard {
  id: string
  name: string
  category?: string | null
  nodes: CanvasNode[]
  edges: CanvasEdge[]
  viewport: Viewport
}
export interface DomainState {
  boards: DomainBoard[]
  activeId: string | null
}
export interface DomainSnapshot {
  state: DomainState
  revision: number
}
const api = axios.create({ baseURL: '/api/domains', timeout: 30000 })
export const loadDomains = () => api.get<DomainSnapshot>('').then((r) => r.data)
export const saveDomains = (state: DomainState, revision: number) =>
  api.put<{ revision: number }>('', { state, revision }).then((r) => r.data)
export const loadDomainPapers = () => api.get<DomainPaper[]>('/papers').then((r) => r.data)

export const newNode = (kind: CanvasNode['kind'], x: number, y: number): CanvasNode => ({
  id: crypto.randomUUID(),
  kind,
  x,
  y,
  width: kind === 'group' ? 640 : 280,
  height: kind === 'group' ? 420 : 160,
  paperId: null,
  groupId: null,
  text: '',
  locked: false,
})

export function freePosition(board: DomainBoard, width: number, height: number, x: number, y: number) {
  // Place incoming batches beside existing content without disturbing it.
  for (let attempt = 0; attempt < board.nodes.length + 1; attempt++) {
    const hit = board.nodes.find(
      (n) =>
        n.kind !== 'group' &&
        x < n.x + n.width + 40 &&
        x + width + 40 > n.x &&
        y < n.y + n.height + 80 &&
        y + height + 40 > n.y,
    )
    if (!hit) break
    y = hit.y + hit.height + 100
  }
  return { x, y }
}
