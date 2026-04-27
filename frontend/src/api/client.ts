import axios from 'axios'

const api = axios.create({ baseURL: '/api', timeout: 30000 })

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

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  ts: string
}

export interface ChatState {
  messages: ChatMessage[]
  thread_created_at: string | null
  expires_at: string | null
  days_remaining: number | null
  ttl_days: number
  ready: boolean
}

export interface PaperDetail extends PaperRecord {
  extracted_text: string | null
  raw_llm_response: string | null
  extraction: Record<string, unknown> | null
  notes: string
  has_first_page_image: boolean
  chat: ChatState
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
  desc: string
  supports_vision?: boolean
}

export interface Config {
  openai_api_key: string
  scan_directory: string
  vlm_model: string
  embedding_model: string
  wiki_compile_model: string
  similarity_threshold: number
  use_first_page_image: boolean
  available_models: AvailableModel[]
  available_embedding_models: AvailableModel[]
  available_wiki_compile_models: AvailableModel[]
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

// Chat — follow-up Q&A against the same Assistants thread used for extraction.
export const getPaperChat = (id: number) =>
  api.get<ChatState>(`/papers/${id}/chat`).then(r => r.data)
export const sendPaperChat = (id: number, message: string) =>
  api.post<ChatState>(`/papers/${id}/chat`, { message }, { timeout: 300000 }).then(r => r.data)
export const resetPaperChat = (id: number) =>
  api.delete<ChatState>(`/papers/${id}/chat`).then(r => r.data)

// Note images — pasted/dropped screenshots embedded in personal notes via markdown.
export const uploadNoteImage = (file: File) => {
  const form = new FormData()
  form.append('file', file)
  return api.post<{ filename: string; url: string; size: number }>(
    '/note_images',
    form,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  ).then(r => r.data)
}

// Graph
export const getGraph = () => api.get<GraphData>('/graph').then(r => r.data)
export const getNode = (id: number) => api.get<NodeDetail>(`/nodes/${id}`).then(r => r.data)
export const searchNodes = (q: string) => api.get('/search', { params: { q } }).then(r => r.data)
export const rebuildEdges = () =>
  api.post<{ threshold: number; total_edges: number }>('/graph/rebuild_edges').then(r => r.data)
export const resetGraph = () => api.post<{ message: string }>('/graph/reset').then(r => r.data)

// Status — short timeout so a single slow tick doesn't block the next
// polling round. The tick handler must tolerate timeouts gracefully.
export const getStatus = () =>
  api.get('/status', { timeout: 8000 }).then(r => r.data)

// Wiki — Phase 1 LLM-compiled concept pages.
// `compiled_at` is sourced from each .md's YAML frontmatter, so the wiki
// remains rebuildable from disk alone (no DB column needed).
export type WikiKind = 'concepts' | 'papers'

// Common fields surfaced to the UI for both wiki page kinds. Kind-specific
// extras (concept_id / paper_id / authors / node_type) are optional and
// populated only when present in the .md frontmatter.
export interface WikiPageMeta {
  filename: string
  // Project-relative path, e.g. data/wiki/papers/0001-xxx.md
  path: string
  // Absolute filesystem path — useful for copying into Finder / Obsidian.
  disk_path: string
  title: string
  slug?: string | null
  compiled_at: string | null
  compile_model: string | null
  size: number
  tags: string[]
  source_paper_ids: number[]
  // concept-only
  concept_id?: number | null
  node_type?: string | null
  // paper-only
  paper_id?: number | null
  authors?: string[]
  source_record?: string | null
}

export interface WikiPageDetail extends WikiPageMeta {
  frontmatter: Record<string, unknown>
  body: string
  raw: string
}

// Backwards-compatible aliases — concept pages used to be the only kind.
export type ConceptPageMeta = WikiPageMeta
export type ConceptPageDetail = WikiPageDetail

export const listConceptPages = () =>
  api.get<{ items: WikiPageMeta[] }>('/wiki/concepts').then(r => r.data.items)
export const getConceptPage = (filename: string) =>
  api.get<WikiPageDetail>(`/wiki/concepts/${encodeURIComponent(filename)}`).then(r => r.data)
export const listPaperPages = () =>
  api.get<{ items: WikiPageMeta[] }>('/wiki/papers').then(r => r.data.items)
export const getPaperPage = (filename: string) =>
  api.get<WikiPageDetail>(`/wiki/papers/${encodeURIComponent(filename)}`).then(r => r.data)
export const recompileConcept = (conceptId: number) =>
  api.post<{ path: string; filename: string }>(
    `/wiki/concepts/${conceptId}/recompile`,
    undefined,
    { timeout: 120000 },
  ).then(r => r.data)
export const recompileAllConcepts = () =>
  api.post<{ message: string }>('/wiki/concepts/recompile').then(r => r.data)
export const recompileAllPaperPages = () =>
  api.post<{ message: string }>('/wiki/papers/recompile').then(r => r.data)

export interface WikiCompileState {
  running: boolean
  kind: 'papers' | 'concepts' | null
  total: number
  done: number
  errors: number
  current: string
  started_at: string | null
  finished_at: string | null
  last_error: string | null
  model: string | null
}

export const getWikiStatus = () =>
  api.get<WikiCompileState>('/wiki/status', { timeout: 8000 }).then(r => r.data)

// Freshness — tells the UI which wiki .md files are out-of-date relative to
// the raw layer (DB). Drives the "X items need recompiling" banner so the
// user doesn't have to track raw-layer changes manually.
export interface FreshnessPaperItem {
  paper_id: number
  title: string
  filename?: string
  processed_at?: string | null
  compiled_at?: string | null
}

export interface FreshnessConceptItem {
  concept_id: number
  title: string
  filename?: string
  node_type?: string | null
  compiled_at?: string | null
  newest_source_processed_at?: string | null
}

export interface FreshnessOrphanItem {
  filename: string
  title?: string | null
}

export interface FreshnessBucket<TMissing, TStale> {
  ok: number
  total_processed?: number
  total_nodes?: number
  missing_count: number
  stale_count: number
  orphan_count: number
  missing: TMissing[]
  stale: TStale[]
  orphan: FreshnessOrphanItem[]
}

export interface WikiFreshnessSummary {
  computed_at: string
  papers: FreshnessBucket<FreshnessPaperItem, FreshnessPaperItem>
  concepts: FreshnessBucket<FreshnessConceptItem, FreshnessConceptItem>
}

export const getWikiFreshness = () =>
  api.get<WikiFreshnessSummary>('/wiki/freshness', { timeout: 12000 }).then(r => r.data)
