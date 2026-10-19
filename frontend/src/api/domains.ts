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
  roadmap?: {
    arxivId: string | null
    doi: string | null
    sourceUrl: string
    reason: string
    branch: string
    libraryPaperId: string | null
    libraryStatus: 'in_library' | 'not_imported' | 'no_arxiv'
    downloadStatus: null | 'queued' | 'running' | 'failed'
    downloadError: string | null
    reviewReady?: boolean
  }
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
  analysis?: DomainAnalysis | null
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

export interface DomainEvidence {
  paperId: string
  source: 'review' | 'wiki' | 'pdf' | 'text'
  locator: string
  page: number | null
  quote: string
}
export interface DomainAnalysis {
  relation: 'builds_on' | 'improves' | 'inspired_by' | 'compares' | 'cites'
  certainty: 'explicit' | 'inferred'
  reason: string
  evidence: DomainEvidence[]
  discussion?: {
    citingPaperId: string
    citedPaperId: string
    status: 'analyzed' | 'reference_only' | 'not_found' | 'unresolved'
    contextsFound: number
    contextsUsed: number
    truncated: boolean
    findings: { aspect: 'purpose' | 'assessment' | 'comparison'; text: string; evidence: DomainEvidence[] }[]
  } | null
}
export interface DomainAgentRun {
  id: string
  boardId: string
  status: 'pending' | 'preview' | 'applied' | 'discarded' | 'cancelled' | 'failed'
  jobId: string | null
  error: string | null
  progress: { label?: string }
  proposal: null | {
    board: DomainBoard
    added: number
    removed: number
    protected: number
    rejected: number
    warnings: { paperId: string; message: string }[]
    unconnected: { paperId: string; title: string }[]
  }
}
export const listDomainRuns = () => api.get<DomainAgentRun[]>('/agent/runs').then(r => r.data)
export const startDomainRun = (boardId: string, revision: number, heights: Record<string, number>) =>
  api.post<DomainAgentRun>('/agent/runs', { boardId, revision, heights }).then(r => r.data)
export const discardDomainRun = (id: string) => api.post<DomainAgentRun>(`/agent/runs/${id}/discard`).then(r => r.data)
export const applyDomainRun = (id: string) =>
  api.post<DomainSnapshot & { before: DomainBoard }>(`/agent/runs/${id}/apply`).then(r => r.data)

/** The two editors share canvas interactions, but never persistence or jobs. */
export interface DomainApi {
  load: typeof loadDomains
  save: typeof saveDomains
  papers: typeof loadDomainPapers
  runs: typeof listDomainRuns
  start: typeof startDomainRun
  discard: typeof discardDomainRun
  apply: typeof applyDomainRun
}
export const domainApi: DomainApi = {
  load: loadDomains, save: saveDomains, papers: loadDomainPapers,
  runs: listDomainRuns, start: startDomainRun, discard: discardDomainRun, apply: applyDomainRun,
}
