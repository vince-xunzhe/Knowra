import axios from 'axios'
import type { DomainApi, DomainAgentRun, DomainBoard, DomainPaper, DomainSnapshot } from './domains'
import type { BackgroundJob } from './jobs'

export interface RoadmapImportProgress {
  label?: string
  total?: number
  done?: number
  imported?: number
  skipped?: number
  failed?: number
  items?: { paperId: string; arxivId: string; status: 'queued' | 'running' | 'imported' | 'skipped' | 'failed'; libraryPaperId: string | null; error: string | null }[]
}
export interface RoadmapImportJob extends BackgroundJob {
  progress: BackgroundJob['progress'] & RoadmapImportProgress
  result: RoadmapImportProgress | null
}
export interface RoadmapReport {
  boardId: string
  runId: string
  markdown: string
  status: 'preview' | 'applied'
}

const api = axios.create({ baseURL: '/api/roadmaps', timeout: 30000 })

export const roadmapApi: DomainApi = {
  load: () => api.get<DomainSnapshot>('').then(r => r.data),
  save: (state, revision) => api.put<{ revision: number }>('', { state, revision }).then(r => r.data),
  papers: () => api.get<DomainPaper[]>('/papers').then(r => r.data),
  runs: () => api.get<DomainAgentRun[]>('/agent/runs').then(r => r.data),
  start: (boardId, revision, heights) => api.post<DomainAgentRun>('/agent/runs', { boardId, revision, heights }).then(r => r.data),
  discard: id => api.post<DomainAgentRun>(`/agent/runs/${id}/discard`).then(r => r.data),
  apply: id => api.post<DomainSnapshot & { before: DomainBoard }>(`/agent/runs/${id}/apply`).then(r => r.data),
}

export const startRoadmapImport = (boardId: string, revision: number) =>
  api.post<RoadmapImportJob>('/imports', { boardId, revision }).then(r => r.data)
export const getRoadmapImport = (boardId: string) =>
  api.get<RoadmapImportJob | null>('/imports', { params: { boardId } }).then(r => r.data)
export const getRoadmapReport = (boardId: string, signal?: AbortSignal) =>
  api.get<RoadmapReport | null>('/report', { params: { boardId }, signal }).then(r => r.data)
