import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

export interface PaperRecord {
  id: number
  filename: string
  filepath: string
  title: string | null
  authors: string[]
  num_pages: number | null
  processed: boolean
  processed_at: string | null
  error: string | null
  created_at: string | null
}

export interface PaperDetail extends PaperRecord {
  extracted_text: string | null
  raw_llm_response: string | null
  extraction: Record<string, unknown> | null
  notes: string
  has_first_page_image: boolean
  knowledge_nodes: { id: number; title: string; node_type: string; tags: string[] }[]
}

export type NodeType =
  | 'paper'
  | 'technique'
  | 'dataset'
  | 'problem_area'
  | 'finding'
  | 'concept'
  | 'entity'
  | 'topic'
  | 'fact'

export interface GraphNode {
  id: string
  title: string
  content: string
  node_type: NodeType
  tags: string[]
  source_paper_ids: number[]
  created_at: string | null
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  relation_type: string
  weight: number
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface NodeDetail extends GraphNode {
  connected_nodes: { id: number; title: string; node_type: string }[]
  edges: { id: number; source: number; target: number; relation_type: string; weight: number }[]
}

export interface AvailableModel {
  id: string
  label: string
  supports_vision: boolean
  desc: string
}

export interface Config {
  openai_api_key: string
  scan_directory: string
  vlm_model: string
  embedding_model: string
  similarity_threshold: number
  use_first_page_image: boolean
  available_models: AvailableModel[]
}

export interface PromptData {
  extraction_prompt: string
  default_prompt: string
}

// Config
export const getConfig = () => api.get<Config>('/config').then(r => r.data)
export const updateConfig = (data: Partial<Omit<Config, 'available_models'>>) =>
  api.post<Config>('/config', data).then(r => r.data)

// Prompt
export const getPrompt = () => api.get<PromptData>('/prompt').then(r => r.data)
export const updatePrompt = (extraction_prompt: string) =>
  api.post<{ message: string; length: number }>('/prompt', { extraction_prompt }).then(r => r.data)
export const resetPrompt = () => api.post<PromptData>('/prompt/reset').then(r => r.data)

// Papers
export const scanPapers = () =>
  api.post<{ new_found: number; total: number; unprocessed: number }>('/scan').then(r => r.data)
export const processAll = () => api.post('/process').then(r => r.data)
export const processPaper = (id: number) => api.post(`/papers/${id}/process`).then(r => r.data)
export const retryPaper = (id: number) => api.post(`/papers/${id}/retry`).then(r => r.data)
export const reprocessPaper = (id: number) => api.post(`/papers/${id}/reprocess`).then(r => r.data)
export const listPapers = () => api.get<PaperRecord[]>('/papers').then(r => r.data)
export const getPaper = (id: number) => api.get<PaperDetail>(`/papers/${id}`).then(r => r.data)
export const updatePaperResponse = (id: number, raw_llm_response: string) =>
  api.put<PaperDetail>(`/papers/${id}/response`, { raw_llm_response }).then(r => r.data)
export const updatePaperNotes = (id: number, notes: string) =>
  api.put<PaperDetail>(`/papers/${id}/notes`, { notes }).then(r => r.data)
export const pdfFileUrl = (id: number) => `/api/papers/${id}/file`
export const firstPageUrl = (id: number) => `/api/papers/${id}/first_page`

// Graph
export const getGraph = () => api.get<GraphData>('/graph').then(r => r.data)
export const getNode = (id: number) => api.get<NodeDetail>(`/nodes/${id}`).then(r => r.data)
export const searchNodes = (q: string) => api.get('/search', { params: { q } }).then(r => r.data)
export const rebuildEdges = () =>
  api.post<{ threshold: number; total_edges: number }>('/graph/rebuild_edges').then(r => r.data)
export const resetGraph = () => api.post<{ message: string }>('/graph/reset').then(r => r.data)

// Status
export const getStatus = () => api.get('/status').then(r => r.data)
