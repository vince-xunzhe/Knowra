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
  paper_category: string | null
  paper_category_model: string | null
  paper_category_override: string | null
  paper_category_source: 'manual' | 'model' | 'none'
  paper_team: string | null
  paper_team_model: string | null
  paper_team_override: string | null
  paper_team_source: 'manual' | 'model' | 'none'
  year: number | null
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
  // Which model produced the current raw_llm_response (e.g. "gpt-4.1",
  // "gpt-5.5"). null for legacy rows that pre-date the column.
  extraction_model: string | null
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
  | 'concept'
  | 'entity'
  | 'topic'
  | 'fact'

export interface GraphNode {
  id: string
  title: string
  content: string
  node_type: NodeType
  category?: string | null
  origin: 'auto' | 'manual'
  hidden: boolean
  concept_candidate: boolean
  publishable_concept: boolean
  promotion_status: 'pending' | 'promoted' | 'rejected'
  promoted_by: 'heuristic' | 'llm' | 'user' | 'legacy' | null
  promotion_reason: string | null
  last_promotion_eval_at: string | null
  tags: string[]
  source_paper_ids: number[]
  paper_id?: number | null
  concept_id?: number | null
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
  connected_nodes: { id: number; title: string; node_type: string; origin: 'auto' | 'manual' }[]
  edges: { id: string | number; source: number; target: number; relation_type: string; weight: number }[]
  linked_papers: { id: number; title: string; filename: string; processed: boolean }[]
  can_hide: boolean
  can_edit: boolean
}

export interface ManualConceptInput {
  title: string
  content: string
  // ids are UUID strings at runtime (legacy INT in older data) — accept both.
  paper_ids: (string | number)[]
  tags: string[]
}

export interface ManualConceptSaveResult {
  node: NodeDetail
  created: boolean
  reused_existing: boolean
  adopted_existing: boolean
  merged_tags: number
  merged_papers: number
  content_applied: boolean
}

export interface AvailableModel {
  id: string
  label: string
  desc: string
  supports_vision?: boolean
  provider_id?: string
  upstream_model?: string
  model_kind?: string
  supported_tasks?: string[]
  builtin?: boolean
}

export interface ProviderTypeOption {
  id: string
  label: string
}

export interface ModelGatewayTaskSpec {
  id: string
  label: string
  description: string
  category: string
  task_type: 'llm' | 'vlm' | 'embedding'
  recommended_model_id?: string
  legacy_field?: string
}

export interface ModelGatewayTaskBinding {
  model_id: string
  reasoning_effort?: 'low' | 'medium' | 'high'
}

export interface ModelGatewayProvider {
  id: string
  label: string
  provider_type: 'openai' | 'openai_compatible' | 'codex_cli'
  base_url?: string
  api_key?: string
  command?: string
  healthcheck_model?: string
  enabled: boolean
  last_tested_at: string | null
  last_test_status: 'ok' | 'error' | 'never'
  last_test_message: string
}

export interface ModelGatewayModel {
  id: string
  label: string
  provider_id: string
  upstream_model: string
  model_kind: 'chat' | 'embedding'
  supports_vision: boolean
  supported_tasks: string[]
  builtin: boolean
}

export interface ModelGatewayConfig {
  providers: ModelGatewayProvider[]
  models: ModelGatewayModel[]
  task_bindings: Record<string, string | ModelGatewayTaskBinding>
  task_specs: ModelGatewayTaskSpec[]
  available_provider_types: ProviderTypeOption[]
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
  available_model_gateway_models: AvailableModel[]
  model_gateway: ModelGatewayConfig
}

export interface PromptData {
  extraction_prompt: string
  default_prompt: string
}

// Config
export const getConfig = () => api.get<Config>('/config').then(r => r.data)
export const updateConfig = (data: Partial<Omit<Config, 'available_models'>>) =>
  api.post<Config>('/config', data).then(r => r.data)
export const testModelGatewayProvider = (providerId: string, modelGateway?: ModelGatewayConfig) =>
  api.post<{ result: { provider_id: string; status: string; message: string; tested_at: string }; config: Config }>(
    `/config/model_gateway/providers/${providerId}/test`,
    modelGateway ? { model_gateway: modelGateway } : undefined,
  ).then(r => r.data)

// Prompt
export const getPrompt = () => api.get<PromptData>('/prompt').then(r => r.data)
export const updatePrompt = (extraction_prompt: string) =>
  api.post<{ message: string; length: number }>('/prompt', { extraction_prompt }).then(r => r.data)
export const resetPrompt = () => api.post<PromptData>('/prompt/reset').then(r => r.data)

// Papers
export const scanPapers = () =>
  api
    .post<{ new_found: number; duplicates: number; total: number; unprocessed: number }>('/scan')
    .then(r => r.data)

export interface UploadResult {
  saved: number
  skipped_existing: number
  rejected: string[]
  new_found: number
  duplicates: number
  total: number
  unprocessed: number
}
// Multipart upload of local PDFs → copied into ./papers, then registered.
// Don't set Content-Type: axios derives the multipart boundary from FormData.
export const uploadPapers = (files: File[]) => {
  const fd = new FormData()
  for (const f of files) fd.append('files', f)
  return api.post<UploadResult>('/papers/upload', fd, { timeout: 180000 }).then(r => r.data)
}
export const processAll = () => api.post('/process').then(r => r.data)
export const processPaper = (id: number) => api.post(`/papers/${id}/process`).then(r => r.data)
export const retryPaper = (id: number) => api.post(`/papers/${id}/retry`).then(r => r.data)
export const retryFailedPapers = () =>
  api.post<{ message: string; retried: number }>('/papers/retry_failed').then(r => r.data)
export const reprocessPaper = (id: number) => api.post(`/papers/${id}/reprocess`).then(r => r.data)
export const listPapers = () => api.get<PaperRecord[]>('/papers').then(r => r.data)
export const getPaper = (id: number) => api.get<PaperDetail>(`/papers/${id}`).then(r => r.data)
export const updatePaperResponse = (
  id: number | string,
  raw_llm_response: string,
  rebuildGraph = true,
) =>
  api
    .put<PaperDetail>(`/papers/${id}/response`, {
      raw_llm_response,
      rebuild_graph: rebuildGraph,
    })
    .then(r => r.data)
export const updatePaperCategory = (id: string | number, category: string | null) =>
  api.put<PaperDetail>(`/papers/${id}/category`, { category }).then(r => r.data)

// ── paper-category taxonomy management ─────────────────────────────────
export interface PaperCategoryItem {
  name: string
  builtin: boolean
  removable: boolean
  count: number
}
export const listPaperCategories = () =>
  api.get<{ categories: PaperCategoryItem[] }>('/paper-categories').then(r => r.data.categories)
export const addPaperCategory = (name: string) =>
  api
    .post<{ categories: PaperCategoryItem[] }>('/paper-categories', { name })
    .then(r => r.data.categories)
export const renamePaperCategory = (name: string, newName: string) =>
  api
    .put<{ categories: PaperCategoryItem[]; migrated: number }>(
      `/paper-categories/${encodeURIComponent(name)}`,
      { new_name: newName },
    )
    .then(r => r.data)
export const deletePaperCategory = (name: string) =>
  api
    .delete<{ categories: PaperCategoryItem[]; migrated: number }>(
      `/paper-categories/${encodeURIComponent(name)}`,
    )
    .then(r => r.data)
export const bulkSetPaperCategory = (paperIds: (string | number)[], category: string | null) =>
  api
    .post<{ updated: number; category: string | null }>('/papers/bulk-category', {
      paper_ids: paperIds,
      category,
    })
    .then(r => r.data)

// ── paper-team dimension (parallel to category) ────────────────────────
export const updatePaperTeam = (id: string | number, team: string | null) =>
  api.put<PaperDetail>(`/papers/${id}/team`, { team }).then(r => r.data)

export interface PaperTeamItem {
  name: string
  authors: string[]
  builtin: boolean
  count: number
}
export interface TeamListResponse {
  teams: PaperTeamItem[]
  others_count: number
}
export const listPaperTeams = () =>
  api.get<TeamListResponse>('/paper-teams').then(r => r.data)
export const addPaperTeam = (name: string, authors: string[] = []) =>
  api.post<TeamListResponse>('/paper-teams', { name, authors }).then(r => r.data)
export const updatePaperTeamRegistry = (
  name: string,
  payload: { new_name?: string; authors?: string[] },
) =>
  api
    .put<TeamListResponse>(`/paper-teams/${encodeURIComponent(name)}`, payload)
    .then(r => r.data)
export const deletePaperTeam = (name: string) =>
  api.delete<TeamListResponse>(`/paper-teams/${encodeURIComponent(name)}`).then(r => r.data)
export const recomputePaperTeams = () =>
  api
    .post<TeamListResponse & { recomputed: number }>('/paper-teams/recompute')
    .then(r => r.data)
export const bulkSetPaperTeam = (paperIds: (string | number)[], team: string | null) =>
  api
    .post<{ updated: number; team: string | null }>('/papers/bulk-team', {
      paper_ids: paperIds,
      team,
    })
    .then(r => r.data)

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
export const getGraph = (includeCandidates = false) =>
  api
    .get<GraphData>('/graph', { params: { include_candidates: includeCandidates } })
    .then(r => r.data)
export const listHiddenGraphNodes = () =>
  api.get<{ nodes: GraphNode[] }>('/graph/hidden_nodes').then(r => r.data.nodes)
export const getNode = (id: string | number) => api.get<NodeDetail>(`/nodes/${id}`).then(r => r.data)
export const searchNodes = (q: string) =>
  api.get<GraphNode[]>('/search', { params: { q } }).then(r => r.data)
export const rebuildEdges = () =>
  api.post<{ threshold: number; total_edges: number }>('/graph/rebuild_edges').then(r => r.data)
export const resetGraph = () => api.post<{ message: string }>('/graph/reset').then(r => r.data)
export const createManualConcept = (data: ManualConceptInput) =>
  api.post<ManualConceptSaveResult>('/graph/manual_concepts', data).then(r => r.data)
export const updateManualConcept = (id: string | number, data: ManualConceptInput) =>
  api.put<ManualConceptSaveResult>(`/graph/manual_concepts/${id}`, data).then(r => r.data)
export const suppressNode = (id: number) =>
  api.post<{ message: string; node_id: number }>(`/graph/nodes/${id}/suppress`).then(r => r.data)
export const restoreNode = (id: number) =>
  api.post<{ node: NodeDetail }>(`/graph/nodes/${id}/restore`).then(r => r.data)

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
export const recompileConcept = (conceptId: string | number) =>
  api.post<{ path: string; filename: string }>(
    `/wiki/concepts/${conceptId}/recompile`,
    undefined,
    { timeout: 120000 },
  ).then(r => r.data)
export const recompilePaper = (paperId: string | number) =>
  api.post<{ path: string; filename: string }>(
    `/wiki/papers/${paperId}/recompile`,
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
  current_item_id?: number | null
  current_item_kind?: 'paper' | 'concept' | null
}

export const getWikiStatus = () =>
  api.get<WikiCompileState>('/wiki/status', { timeout: 8000 }).then(r => r.data)

export interface WikiGraphNode {
  id: string
  kind: 'group' | 'paper' | 'concept'
  title: string
  subtitle?: string | null
  year?: number | null
  filename?: string | null
  page_kind?: 'papers' | 'concepts' | null
  paper_id?: number | null
  concept_id?: number | null
  node_type?: string | null
  category?: string | null
  compiled_at?: string | null
  x: number
  y: number
  active: boolean
}

export interface WikiGraphEdge {
  id: string
  source: string
  target: string
  relation_type: 'timeline' | 'supports' | string
  node_type?: string | null
  category?: string | null
}

export interface WikiGraphSummary {
  name: string
  paper_count: number
  concept_count: number
}

export interface WikiGraphData {
  updated_at: string
  categories: WikiGraphSummary[]
  nodes: WikiGraphNode[]
  edges: WikiGraphEdge[]
}

export const getWikiGraph = () =>
  api.get<WikiGraphData>('/wiki/graph', { timeout: 12000 }).then(r => r.data)

// Freshness — tells the UI which wiki .md files are out-of-date relative to
// the raw layer (DB). Drives the "X items need recompiling" banner so the
// user doesn't have to track raw-layer changes manually.
export interface FreshnessPaperItem {
  paper_id: string
  title: string
  filename?: string
  processed_at?: string | null
  compiled_at?: string | null
}

export interface FreshnessConceptItem {
  concept_id: string
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

// FTS5 search across the LLM-compiled wiki layer. Pure local SQLite —
// zero token cost. snippet contains <mark>...</mark> spans; render via
// dangerouslySetInnerHTML.
export interface WikiSearchHit {
  kind: 'paper' | 'concept'
  filename: string
  path: string
  title: string
  compiled_at: string | null
  snippet: string
  score: number
  paper_id?: number | null
  concept_id?: number | null
}

export interface WikiSearchResponse {
  query: string
  hits: WikiSearchHit[]
}

export const searchWiki = (q: string, limit = 20) =>
  api.get<WikiSearchResponse>('/wiki/search', {
    params: { q, limit },
    timeout: 10000,
  }).then(r => r.data)

// --- Wiki Ask agent + index --------------------------------------------

export interface AskTraceStep {
  step: number
  tool: 'list_wiki_index' | 'search_wiki' | 'read_wiki' | string
  args: Record<string, unknown>
  result_summary: string
  duration_ms: number
}

export interface AskCitation {
  kind: 'paper' | 'concept' | 'unknown' | string
  ref: string
  path?: string | null
  filename?: string | null
  paper_id?: number | null
}

export interface AskResponse {
  answer: string
  cited_files: string[]
  citations: AskCitation[]
  trace: AskTraceStep[]
  model: string
  session_title?: string | null
  session_id?: string | null
  duration_ms: number
  steps: number
}

export const askWiki = (
  question: string,
  history?: { role: 'user' | 'assistant'; content: string }[],
  sessionId?: string,
) =>
  api
    .post<AskResponse>(
      '/wiki/ask',
      { question, history, session_id: sessionId },
      { timeout: 600000 }, // agent loops can run minutes on cold cache
    )
    .then(r => r.data)

export interface WikiIndexStatus {
  exists: boolean
  path: string
  size: number
  modified_at?: string
  indexed_at?: string | null
  indexed_papers?: number | null
  indexed_concepts?: number | null
  current_papers?: number
  current_concepts?: number
  /** True when index.md exists but its recorded counts don't match the
   *  current wiki — user should rebuild for the agent to see new pages. */
  stale?: boolean
}

export const getWikiIndexStatus = () =>
  api.get<WikiIndexStatus>('/wiki/index/status').then(r => r.data)

export const getWikiIndex = () =>
  api
    .get<{ text: string; summary: WikiIndexStatus }>('/wiki/index')
    .then(r => r.data)

export const rebuildWikiIndex = () =>
  api
    .post<{ path: string; size: number }>(
      '/wiki/index/rebuild',
      undefined,
      { timeout: 300000 },
    )
    .then(r => r.data)

export interface SynthesisConceptInput {
  title: string
  body: string
  source_question?: string
  source_questions?: string[]
  synthesis_scope?: 'turn' | 'session'
  source_session_id?: string
  source_session_title?: string
  source_turn_indexes?: number[]
  source_cited_files?: string[]
  force_create?: boolean
  source_paper_ids?: number[]
  tags?: string[]
}

export interface SynthesisConceptResult {
  concept_id: string | number
  filename: string
  path: string
  created: boolean
  reused_existing: boolean
  forced_create: boolean
  concept_title?: string
  analysis_used?: boolean
  analysis_model?: string | null
  related_concepts_added?: number
}

export const createSynthesisConcept = (payload: SynthesisConceptInput) =>
  api
    .post<SynthesisConceptResult>(
      '/wiki/concepts/from_synthesis',
      payload,
    )
    .then(r => r.data)

export type WikiOutputFormat = 'marp' | 'report'

export interface RenderOutputResult {
  kind: string
  filename: string
  path: string
  rel_path: string
}

export const renderWikiOutput = (payload: {
  answer: string
  format: WikiOutputFormat
  title: string
  source_question?: string
}) =>
  api
    .post<RenderOutputResult>('/wiki/outputs/render', payload, {
      timeout: 300000,
    })
    .then(r => r.data)

// --- P1: wiki content lint / health-check ------------------------------

export interface LintStub {
  // IDs are UUID strings post-multitenant migration.
  concept_id: string
  title: string
  node_type: string
  source_paper_count: number
  body_word_len: number
  filename: string | null
  reasons: string[]
  excerpt: string
}

export interface LintMerge {
  a_id: string
  a_title: string
  b_id: string
  b_title: string
  cosine: number
  paper_overlap: number
  paper_jaccard: number
  score: number
}

export interface LintCrosscut {
  paper_ids: number[]
  paper_titles: string[]
  size: number
}

export interface LintJudgment {
  used_model: boolean
  model?: string | null
  error?: string
  stubs?: { concept_id: string; verdict: string; reason: string }[]
  merges?: {
    a_id: string
    b_id: string
    should_merge: boolean
    keep: string | null
    reason: string
  }[]
  new_concepts?: { title: string; paper_ids: string[]; rationale: string }[]
  followups?: string[]
}

export interface LintResult {
  generated_at: string
  counts: {
    concepts_scanned: number
    stubs: number
    merges: number
    missing_crosscut: number
    followups: number
  }
  stubs: LintStub[]
  merges: LintMerge[]
  missing_crosscut: LintCrosscut[]
  judgment: LintJudgment
  report_path: string
  report_rel_path: string
}

export interface LintReportStatus {
  exists: boolean
  rel_path?: string
  size?: number
  modified_at?: string
}

export const runWikiLint = (useLlm = true) =>
  api
    .post<LintResult>('/wiki/lint/run', { use_llm: useLlm }, { timeout: 300000 })
    .then(r => r.data)

export const getWikiLintStatus = () =>
  api.get<LintReportStatus>('/wiki/lint/status').then(r => r.data)

export const getWikiLintReport = () =>
  api
    .get<{ text: string; status: LintReportStatus }>('/wiki/lint/report')
    .then(r => r.data)

export const acceptLintStub = (conceptId: string | number) =>
  api
    .post<{ ok: boolean; concept_id: string; tag: string }>(
      '/wiki/lint/accept',
      { concept_id: conceptId },
    )
    .then(r => r.data)

// --- Concept promotion -----------------------------------------------------
//
// Concept-eligible nodes (technique / dataset / problem_area / concept) move
// through pending → promoted | rejected. Heuristic + LLM proposes; the user
// has the final word. See backend/routers/promotion.py.

export type PromotionStatus = 'pending' | 'promoted' | 'rejected'
export type PromotedBy = 'heuristic' | 'llm' | 'user' | 'legacy' | null

export interface PromotionCandidate {
  id: number
  title: string
  node_type: string
  tags: string[]
  source_paper_ids: number[]
  promotion_status: PromotionStatus
  promoted_by: PromotedBy
  promotion_reason: string | null
  last_promotion_eval_at: string | null
}

export interface PromotionCounts {
  pending: number
  promoted: number
  rejected: number
}

// Wider summary returned by /counts, /run, /accept_llm, /bulk so the
// candidate panel can render the lifecycle state in one shot — counts +
// promoted_by breakdown ("human N / agent M") + the freshest eval
// timestamp.
export interface PromotionSummary {
  counts: PromotionCounts
  by: {
    user: number
    llm: number
    heuristic: number
    legacy: number
    unset: number
    [extra: string]: number
  }
  last_eval_at: string | null
  total_candidates: number
  decided: number
}

export interface PromotionRunResponse {
  heuristic: {
    promoted: number
    rejected: number
    deferred: number
    total_evaluated: number
    skipped_user_pinned: number
  }
  llm:
    | null
    | { error: string }
    | {
        promoted: number
        rejected: number
        still_ambiguous: number
        total_evaluated: number
        model: string
      }
  counts: PromotionCounts
  summary: PromotionSummary
}

export const runPromotion = (params: { force_all?: boolean; use_llm?: boolean } = {}) =>
  api
    .post<PromotionRunResponse>('/promotion/run', params, { timeout: 600000 })
    .then(r => r.data)

export const listPromotionCandidates = (status?: PromotionStatus, limit = 500) =>
  api
    .get<{ items: PromotionCandidate[]; counts: PromotionCounts }>(
      '/promotion/candidates',
      { params: { status, limit } },
    )
    .then(r => r.data)

export const updatePromotionStatus = (
  nodeId: string | number,
  status: PromotionStatus,
  reason?: string,
) =>
  api
    .patch<{ node: PromotionCandidate }>(`/promotion/${nodeId}`, { status, reason })
    .then(r => r.data)

export const getPromotionCounts = () =>
  api
    .get<{ counts: PromotionCounts; summary: PromotionSummary }>('/promotion/counts')
    .then(r => r.data)

export interface PromotionPromptPayload {
  prompt: string
  default_template: string
}

export const getPromotionPrompt = () =>
  api.get<PromotionPromptPayload>('/promotion/prompt').then(r => r.data)

export const updatePromotionPrompt = (prompt: string) =>
  api
    .put<{ prompt: string }>('/promotion/prompt', { prompt })
    .then(r => r.data)

export const acceptLLMProposals = () =>
  api
    .post<{ locked: number; counts: PromotionCounts; summary: PromotionSummary }>(
      '/promotion/accept_llm',
    )
    .then(r => r.data)

export const bulkUpdatePromotion = (
  nodeIds: number[],
  status: PromotionStatus,
  reason?: string,
) =>
  api
    .post<{ changed: number; counts: PromotionCounts; summary: PromotionSummary }>(
      '/promotion/bulk',
      { node_ids: nodeIds, status, reason },
    )
    .then(r => r.data)

// --- Dashboard ---------------------------------------------------------
//
// All widgets on the [看板] page consume a single fat payload so we can
// be sure every chart reflects the same snapshot. See
// backend/routers/dashboard.py for the field-by-field contract.

export interface DashboardOverview {
  papers: number
  papers_processed: number
  papers_unprocessed: number
  papers_failed: number
  nodes: number
  concepts_promoted: number
  edges: number
  unique_tags: number
}

export interface DashboardRadarPoint {
  tag: string
  papers: number
  concepts: number
  edge_density: number
}

export interface DashboardGrowth {
  weeks: string[]
  papers: number[]
  concepts: number[]
  edges: number[]
}

export interface DashboardSlice {
  label: string
  value: number
}

export interface DashboardCurationCell {
  status: 'pending' | 'promoted' | 'rejected'
  by: 'human' | 'agent' | 'heuristic' | 'legacy' | 'unset' | string
  count: number
}

export interface DashboardHub {
  // Post-W3.2 backend returns UUID strings here. The widget only uses
  // id as a React list key, so a string works the same.
  id: string
  title: string
  node_type: string
  degree: number
}

export interface DashboardNetwork {
  hubs: DashboardHub[]
  orphan_count: number
  avg_degree: number
  relation_types: DashboardSlice[]
}

export interface DashboardCompileBucket {
  ok: number
  missing: number
  stale: number
  orphan: number
  total: number
}

export interface DashboardLintCounts {
  stubs: number
  merges: number
  missing_crosscut: number
  followups: number
}

export interface DashboardLint {
  exists: boolean
  modified_at?: string
  size?: number
  counts?: DashboardLintCounts
}

export interface DashboardLLMUsageByTask {
  task: string
  calls: number
  total_tokens: number
  avg_latency_ms: number | null
}

export interface DashboardLLMUsageByModel {
  model: string
  provider: string
  calls: number
  total_tokens: number
  avg_latency_ms: number | null
}

export interface DashboardLLMUsage {
  window_days: number
  total_calls: number
  total_tokens: number
  success_rate: number
  by_task: DashboardLLMUsageByTask[]
  by_model: DashboardLLMUsageByModel[]
}

export interface DashboardSummary {
  generated_at: string
  overview: DashboardOverview
  radar: DashboardRadarPoint[]
  growth: DashboardGrowth
  distribution: {
    paper_category: DashboardSlice[]
    node_type: DashboardSlice[]
  }
  top_tags: DashboardSlice[]
  curation: DashboardCurationCell[]
  pending_age_days: number | null
  network: DashboardNetwork
  compile: {
    papers: DashboardCompileBucket
    concepts: DashboardCompileBucket
  }
  lint: DashboardLint
  llm_usage: DashboardLLMUsage
}

export const getDashboardSummary = () =>
  api.get<DashboardSummary>('/dashboard/summary', { timeout: 20000 }).then(r => r.data)
