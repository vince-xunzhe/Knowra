/**
 * Cloud API client — Supabase Auth (REST) + Knowra sync / cloud endpoints.
 *
 * Why no `@supabase/supabase-js`? We only need three things from Supabase
 * here: password sign-in/sign-up, token refresh, and a stored session.
 * Pulling the whole SDK + GoTrue client for that would balloon the
 * frontend bundle and tie us to its schema-evolution churn. axios is
 * already in `package.json`, and Supabase Auth speaks plain JSON over
 * HTTPS, so we wrap the four endpoints we actually use.
 *
 * Configuration source of truth: localStorage (so the user can set them
 * via Settings → 云同步 at runtime). Environment defaults from Vite are
 * used if localStorage is empty — useful for dev where you want the
 * .env.local to provide test values automatically.
 *
 * NOTE: this file is the desktop ↔ cloud bridge. It must NEVER touch the
 * user's OpenAI key — that's settings on this device only. The cloud
 * Ask endpoint takes the key in the request body and the cloud backend
 * drops it after the call (see backend/services/cloud_ask.py).
 */
import axios, { AxiosError, type AxiosInstance } from 'axios'

// ── runtime config (localStorage-first) ─────────────────────────────────

const LS_KEYS = {
  supabaseUrl: 'knowra.cloud.supabaseUrl',
  supabaseAnonKey: 'knowra.cloud.supabaseAnonKey',
  baseUrl: 'knowra.cloud.baseUrl',
  session: 'knowra.cloud.session',
  deviceId: 'knowra.cloud.deviceId',
  lastSyncAt: 'knowra.cloud.lastSyncAt',
} as const

function readLs(key: string): string {
  try {
    return localStorage.getItem(key) ?? ''
  } catch {
    return ''
  }
}

function writeLs(key: string, value: string | null) {
  try {
    if (value === null || value === '') localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    // Storage unavailable (e.g. quota / Safari private). Cloud sync is
    // best-effort here — surface elsewhere if write actually matters.
  }
}

export interface CloudConfig {
  supabaseUrl: string
  supabaseAnonKey: string
  baseUrl: string
}

/**
 * Baked-in production cloud config. PUBLIC values — the Supabase project URL,
 * its anon/publishable key, and the Fly backend URL — safe to ship: per-user
 * isolation is enforced by Supabase RLS + each user's own login, NOT by hiding
 * these (same model as Firebase's apiKey). Means a fresh install can sign in
 * with no setup. Precedence: localStorage override → Vite env (.env.local for
 * dev) → these defaults.
 */
export const CLOUD_DEFAULTS: CloudConfig = {
  supabaseUrl: 'https://umflsxjvndppadtnfxke.supabase.co',
  supabaseAnonKey:
    'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVtZmxzeGp2bmRwcGFkdG5meGtlIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA0MDkwMDEsImV4cCI6MjA5NTk4NTAwMX0.hClTx8Zt7nFCIMw62b710zx-uaFKcDHdfAkyw45pFJM',
  baseUrl: 'https://knowra-cloud.fly.dev',
}

export function getCloudConfig(): CloudConfig {
  // Vite injects literal env vars at build time via `import.meta.env`.
  // We *read* from it, never assign — assigning trips Vite's HMR
  // transform into writing `import.meta.env = {...}` into the served
  // module, which throws at runtime since import.meta is read-only.
  // Hence the indirect Record cast rather than the obvious pattern.
  const env = import.meta.env as unknown as Record<string, string | undefined>
  return {
    supabaseUrl: readLs(LS_KEYS.supabaseUrl) || env.VITE_SUPABASE_URL || CLOUD_DEFAULTS.supabaseUrl,
    supabaseAnonKey: readLs(LS_KEYS.supabaseAnonKey) || env.VITE_SUPABASE_ANON_KEY || CLOUD_DEFAULTS.supabaseAnonKey,
    baseUrl: readLs(LS_KEYS.baseUrl) || env.VITE_KNOWRA_CLOUD_BASE_URL || CLOUD_DEFAULTS.baseUrl,
  }
}

export function setCloudConfig(next: Partial<CloudConfig>) {
  if (next.supabaseUrl !== undefined) writeLs(LS_KEYS.supabaseUrl, next.supabaseUrl.trim() || null)
  if (next.supabaseAnonKey !== undefined) writeLs(LS_KEYS.supabaseAnonKey, next.supabaseAnonKey.trim() || null)
  if (next.baseUrl !== undefined) writeLs(LS_KEYS.baseUrl, next.baseUrl.trim().replace(/\/+$/, '') || null)
}

export function isCloudConfigured(): boolean {
  const c = getCloudConfig()
  return Boolean(c.supabaseUrl && c.supabaseAnonKey && c.baseUrl)
}

export function getDeviceId(): string {
  let id = readLs(LS_KEYS.deviceId)
  if (!id) {
    // RFC4122 v4-ish via crypto if available, else fall back to random
    id = (typeof crypto !== 'undefined' && 'randomUUID' in crypto)
      ? crypto.randomUUID()
      : `dev-${Math.random().toString(36).slice(2)}-${Date.now()}`
    writeLs(LS_KEYS.deviceId, id)
  }
  return id
}

// ── session storage ─────────────────────────────────────────────────────

export interface CloudUser {
  id: string
  email: string | null
  display_name: string | null
}

export interface CloudSession {
  access_token: string
  refresh_token: string
  /** Unix ms when access_token expires. */
  expires_at: number
  user: CloudUser
}

export function getStoredSession(): CloudSession | null {
  const raw = readLs(LS_KEYS.session)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as CloudSession
    if (!parsed.access_token || !parsed.refresh_token) return null
    return parsed
  } catch {
    return null
  }
}

function storeSession(session: CloudSession | null) {
  writeLs(LS_KEYS.session, session ? JSON.stringify(session) : null)
}

export function getLastSyncAt(): string | null {
  return readLs(LS_KEYS.lastSyncAt) || null
}

export function setLastSyncAt(iso: string) {
  writeLs(LS_KEYS.lastSyncAt, iso)
}

// ── Supabase Auth REST ──────────────────────────────────────────────────

interface SupabaseAuthError {
  error?: string
  error_description?: string
  msg?: string
  message?: string
}

export class CloudAuthError extends Error {
  readonly code?: string
  constructor(message: string, code?: string) {
    super(message)
    this.name = 'CloudAuthError'
    this.code = code
  }
}

function authClient(): AxiosInstance {
  const { supabaseUrl, supabaseAnonKey } = getCloudConfig()
  if (!supabaseUrl || !supabaseAnonKey) {
    throw new CloudAuthError('未配置 Supabase URL / anon key，请到设置 → 云同步填写', 'not_configured')
  }
  return axios.create({
    baseURL: `${supabaseUrl.replace(/\/+$/, '')}/auth/v1`,
    headers: {
      apikey: supabaseAnonKey,
      'Content-Type': 'application/json',
    },
    timeout: 20000,
  })
}

function toAuthError(err: unknown): CloudAuthError {
  if (err instanceof CloudAuthError) return err
  const ax = err as AxiosError<SupabaseAuthError>
  const data = ax.response?.data
  const msg =
    data?.error_description || data?.msg || data?.message || data?.error ||
    ax.message || '认证失败'
  return new CloudAuthError(msg, data?.error || `http_${ax.response?.status ?? 'err'}`)
}

interface SupabaseTokenResponse {
  access_token: string
  refresh_token: string
  expires_in: number
  expires_at?: number
  user?: { id: string; email?: string | null; user_metadata?: Record<string, unknown> }
}

function sessionFromToken(t: SupabaseTokenResponse): CloudSession {
  const expiresAtMs = t.expires_at
    ? t.expires_at * 1000
    : Date.now() + t.expires_in * 1000
  const meta = t.user?.user_metadata || {}
  const displayName = (meta.display_name || meta.full_name || meta.name) as string | undefined
  return {
    access_token: t.access_token,
    refresh_token: t.refresh_token,
    expires_at: expiresAtMs,
    user: {
      id: t.user?.id || '',
      email: t.user?.email ?? null,
      display_name: displayName ?? null,
    },
  }
}

export async function cloudSignIn(email: string, password: string): Promise<CloudSession> {
  try {
    const { data } = await authClient().post<SupabaseTokenResponse>(
      '/token?grant_type=password',
      { email, password },
    )
    const session = sessionFromToken(data)
    storeSession(session)
    return session
  } catch (err) {
    throw toAuthError(err)
  }
}

export async function cloudSignUp(email: string, password: string): Promise<CloudSession | null> {
  try {
    const { data } = await authClient().post<SupabaseTokenResponse | {
      // signup without auto-confirm returns a user but no tokens
      id: string
      email?: string | null
      confirmation_sent_at?: string
    }>('/signup', { email, password })
    if ('access_token' in data && data.access_token) {
      const session = sessionFromToken(data)
      storeSession(session)
      return session
    }
    // Email confirmation required: caller decides what to display.
    return null
  } catch (err) {
    throw toAuthError(err)
  }
}

export async function cloudRefreshSession(refreshToken: string): Promise<CloudSession> {
  try {
    const { data } = await authClient().post<SupabaseTokenResponse>(
      '/token?grant_type=refresh_token',
      { refresh_token: refreshToken },
    )
    const session = sessionFromToken(data)
    storeSession(session)
    return session
  } catch (err) {
    throw toAuthError(err)
  }
}

export async function cloudSignOut(): Promise<void> {
  const session = getStoredSession()
  storeSession(null)
  if (!session) return
  try {
    await authClient().post('/logout', undefined, {
      headers: { Authorization: `Bearer ${session.access_token}` },
    })
  } catch {
    // Logout is best-effort: the local token is already cleared, and
    // Supabase will GC the refresh token server-side eventually.
  }
}

/** Returns a valid access token, refreshing if it's within `bufferMs` of
 *  expiry. Throws CloudAuthError if no session is stored or refresh fails. */
export async function getFreshAccessToken(bufferMs = 60_000): Promise<string> {
  const session = getStoredSession()
  if (!session) throw new CloudAuthError('未登录', 'no_session')
  if (Date.now() < session.expires_at - bufferMs) return session.access_token
  const refreshed = await cloudRefreshSession(session.refresh_token)
  return refreshed.access_token
}

// ── Knowra cloud client (authenticated) ─────────────────────────────────

function cloudClient(): AxiosInstance {
  const { baseUrl } = getCloudConfig()
  if (!baseUrl) {
    throw new CloudAuthError('未配置云后端 URL，请到设置 → 云同步填写', 'not_configured')
  }
  const instance = axios.create({
    baseURL: baseUrl.replace(/\/+$/, ''),
    // 5 min: prepare with N wiki files makes N serial signed-URL
    // requests to Supabase (across regions). With 121 wikis at
    // ~200ms each that's ~25s; we leave headroom for first-sync
    // libraries with hundreds of files. Per-PUT uploads use their
    // own raw fetch with no timeout cap.
    timeout: 300_000,
  })
  // Inject fresh access token per request, auto-refresh on 401.
  instance.interceptors.request.use(async config => {
    const token = await getFreshAccessToken()
    config.headers = config.headers ?? {}
    ;(config.headers as Record<string, string>).Authorization = `Bearer ${token}`
    return config
  })
  return instance
}

// ── sync payload shapes (mirror backend/schemas/sync.py) ───────────────

export const SYNC_API_VERSION = '1'

export interface PaperRow {
  id: string
  user_id: string
  updated_at?: string | null
  filepath: string
  filename: string
  file_hash: string
  title?: string | null
  authors?: unknown[] | null
  num_pages?: number | null
  processed?: boolean | null
  processed_at?: string | null
  extraction_model?: string | null
  paper_category_model?: string | null
  paper_category_override?: string | null
  paper_team_model?: string | null
  paper_team_override?: string | null
  raw_llm_response?: string | null
  notes?: string | null
  error?: string | null
  processing_status?: string | null
  retry_count?: number | null
  last_error_stage?: string | null
  last_error_reason?: string | null
  last_error_recoverable?: boolean | null
  legacy_id?: number | null
  created_at?: string | null
}

export interface KnowledgeNodeRow {
  id: string
  user_id: string
  updated_at?: string | null
  title: string
  content: string
  node_type?: string | null
  node_origin?: string | null
  hidden?: boolean | null
  promotion_status?: string | null
  promoted_by?: string | null
  promotion_reason?: string | null
  last_promotion_eval_at?: string | null
  tags?: unknown[] | null
  embedding?: number[] | null
  source_paper_ids?: string[] | null
  legacy_id?: number | null
  created_at?: string | null
}

export interface KnowledgeEdgeRow {
  id: string
  user_id: string
  updated_at?: string | null
  source_id: string
  target_id: string
  relation_type?: string | null
  weight?: number | null
  legacy_id?: number | null
  created_at?: string | null
}

export interface WikiFileRow {
  id: string
  user_id: string
  updated_at?: string | null
  kind: 'paper' | 'concept' | 'index' | 'lint_report' | string
  rel_path: string
  content_hash: string
  size_bytes: number
  title?: string | null
  aliases?: string[] | null
  compiled_at?: string | null
  paper_id?: string | null
  concept_id?: string | null
}

export interface SyncTables {
  papers?: PaperRow[]
  knowledge_nodes?: KnowledgeNodeRow[]
  knowledge_edges?: KnowledgeEdgeRow[]
  wiki_files?: WikiFileRow[]
}

export interface SyncDeletions {
  papers?: string[]
  knowledge_nodes?: string[]
  knowledge_edges?: string[]
  wiki_files?: string[]
}

export interface PrepareRequest {
  api_version?: string
  device_id: string
  since?: string | null
  tables?: SyncTables
  deletions?: SyncDeletions
}

export interface UploadInstruction {
  rel_path: string
  upload_url: string
  method: string
  headers: Record<string, string>
}

export interface SkippedUpload {
  rel_path: string
  reason: string
}

export interface ValidationError {
  table: string
  id?: string | null
  reason: string
  code?: string | null
}

export interface PrepareResponse {
  sync_session_id: string
  expires_at: string
  uploads_required: UploadInstruction[]
  uploads_skipped: SkippedUpload[]
  validation_errors: ValidationError[]
}

export interface CommitFileEntry {
  rel_path: string
  content_hash: string
}

export interface CommitRequest {
  api_version?: string
  sync_session_id: string
  uploaded: CommitFileEntry[]
}

export interface CommitAccepted {
  papers: number
  knowledge_nodes: number
  knowledge_edges: number
  wiki_files: number
}

export interface CommitRejection {
  table: string
  id?: string | null
  rel_path?: string | null
  reason: string
  code: string
}

export interface CommitResponse {
  revision: number
  accepted: CommitAccepted
  rejected: CommitRejection[]
  server_now: string
}

// ── cloud read / Ask shapes (mirror backend/schemas/cloud.py) ──────────

export interface MeStats {
  papers: number
  concepts: number
  edges: number
  wiki_files: number
  last_desktop_sync_at?: string | null
  wiki_size_bytes: number
}

export interface MeResponse {
  user_id: string
  email?: string | null
  display_name?: string | null
  stats: MeStats
}

// ── transient-failure retry ─────────────────────────────────────────────

/** A call failed in a way that's worth retrying: no HTTP response at
 *  all (network reset / cold-start 503 from the Fly proxy with no CORS
 *  headers — surfaces as ERR_NETWORK), a 502/503/504, or a timeout.
 *  We do NOT retry 4xx (auth, validation, conflict) — those won't fix
 *  themselves on retry. */
function isRetriable(err: unknown): boolean {
  const ax = err as AxiosError
  // No response object → network error / CORS-blocked preflight /
  // connection reset. The single biggest source of flakiness on a
  // cross-border link to Fly, and also exactly what a cold-starting
  // machine produces.
  if (ax?.isAxiosError && !ax.response) return true
  const status = ax?.response?.status
  return status === 502 || status === 503 || status === 504
}

/**
 * Run an async call with bounded exponential-backoff retries on
 * transient failures. Used for the sync endpoints because the
 * desktop talks to Fly across the Pacific, where a single dropped
 * packet or a machine waking from idle should NOT surface as a hard
 * "Network Error" to the user.
 *
 * attempts=4 with base 800ms → waits ~0.8s, 1.6s, 3.2s between tries
 * (≈5.6s total worst case before giving up). prepare/commit are
 * idempotent server-side (sync_session replay), so retrying is safe.
 */
async function withRetry<T>(
  fn: () => Promise<T>,
  { attempts = 4, baseDelayMs = 800, label = 'request' }: {
    attempts?: number
    baseDelayMs?: number
    label?: string
  } = {},
): Promise<T> {
  let lastErr: unknown
  for (let i = 0; i < attempts; i += 1) {
    try {
      return await fn()
    } catch (err) {
      lastErr = err
      if (i === attempts - 1 || !isRetriable(err)) break
      const delay = baseDelayMs * 2 ** i
      // eslint-disable-next-line no-console
      console.warn(`[cloud] ${label} failed (attempt ${i + 1}/${attempts}), retrying in ${delay}ms`, err)
      await new Promise(res => setTimeout(res, delay))
    }
  }
  throw lastErr
}

// ── endpoint wrappers ──────────────────────────────────────────────────

export const cloudPrepare = (req: PrepareRequest) =>
  withRetry(
    () => cloudClient()
      .post<PrepareResponse>('/api/sync/prepare', { api_version: SYNC_API_VERSION, ...req })
      .then(r => r.data),
    { label: 'prepare' },
  )

export const cloudCommit = (req: CommitRequest) =>
  withRetry(
    () => cloudClient()
      .post<CommitResponse>('/api/sync/commit', { api_version: SYNC_API_VERSION, ...req })
      .then(r => r.data),
    { label: 'commit' },
  )

export const cloudMe = () =>
  withRetry(
    () => cloudClient().get<MeResponse>('/api/cloud/me').then(r => r.data),
    { label: 'me' },
  )

// ── recommendations (arXiv feed) ────────────────────────────────────────

export interface RecTag {
  name: string
}

export interface RecItem {
  id: string
  tag: string
  arxiv_id: string
  title: string
  authors: string[]
  abstract: string | null
  pdf_url: string | null
  primary_category: string | null
  published: string | null
  created_at: string | null
}

export interface RecommendationsResponse {
  tags: RecTag[]
  items: RecItem[]
  days: number
}

export const cloudRecommendations = (days = 7) =>
  withRetry(
    () =>
      cloudClient()
        .get<RecommendationsResponse>('/api/cloud/recommendations', { params: { days } })
        .then(r => r.data),
    { label: 'recommendations' },
  )

export const cloudRecTags = () =>
  withRetry(
    () => cloudClient().get<{ tags: RecTag[] }>('/api/cloud/rec-tags').then(r => r.data.tags),
    { label: 'rec-tags' },
  )

export const cloudRefreshRecommendations = () =>
  cloudClient()
    .post<{ added: number; pruned: number; tags: string[] }>('/api/cloud/recommendations/refresh')
    .then(r => r.data)

/** Wake the cloud machine and wait until it's serving before a sync.
 *  With min_machines_running=1 this is normally instant, but if the
 *  machine was restarted (deploy) or the platform bounced it, the
 *  first call may hit a brief window. Hitting /api/cloud/me (which
 *  401s without auth but proves the app is up) through withRetry
 *  rides out that window. Returns true once reachable. */
export async function cloudWarmup(): Promise<boolean> {
  const { baseUrl } = getCloudConfig()
  if (!baseUrl) return false
  try {
    await withRetry(
      async () => {
        // Bare axios (no auth interceptor) — we only need to prove the
        // server answers HTTP. 401 is success here.
        const res = await axios.get(`${baseUrl.replace(/\/+$/, '')}/api/cloud/me`, {
          timeout: 15_000,
          validateStatus: () => true, // any HTTP response = machine awake
        })
        if (res.status >= 500) {
          throw Object.assign(new Error(`warmup got ${res.status}`), {
            isAxiosError: true, response: res,
          })
        }
        return res
      },
      { attempts: 6, baseDelayMs: 1500, label: 'warmup' },
    )
    return true
  } catch {
    return false
  }
}

/**
 * Performs the signed PUT upload Supabase returned. Uses raw fetch
 * (not the axios client) because:
 *   1. We don't want to inject our Bearer token here — the URL is
 *      already signed.
 *   2. axios will choke trying to parse non-JSON responses; fetch is
 *      lighter for binary PUT.
 */
export async function performSignedUpload(
  instr: UploadInstruction,
  body: Blob | ArrayBuffer | string,
): Promise<void> {
  // Up to 3 attempts: signed PUTs go straight to Supabase Storage
  // (Sydney) and a dropped connection mid-upload shouldn't fail the
  // whole sync. The signed URL is valid for minutes, so re-PUTting
  // the same bytes is safe + idempotent (we send x-upsert).
  let lastErr: unknown
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const res = await fetch(instr.upload_url, {
        method: instr.method || 'PUT',
        headers: instr.headers || {},
        body: body as BodyInit,
      })
      if (res.ok) return
      // 5xx from Storage is transient; 4xx (e.g. expired token) is not.
      if (res.status < 500) {
        const text = await res.text().catch(() => '')
        throw new Error(`signed upload failed ${res.status}: ${text.slice(0, 200)}`)
      }
      lastErr = new Error(`signed upload ${res.status}`)
    } catch (err) {
      lastErr = err // network error (TypeError: Failed to fetch) → retry
    }
    if (attempt < 2) await new Promise(r => setTimeout(r, 700 * 2 ** attempt))
  }
  throw lastErr instanceof Error ? lastErr : new Error('signed upload failed')
}

// ── small helpers callers will want ────────────────────────────────────

/** SHA-256 hex of arbitrary bytes via WebCrypto. Used to compute
 *  content_hash for wiki files before /prepare. */
export async function sha256Hex(data: ArrayBuffer | Uint8Array | string): Promise<string> {
  // Normalize to a fresh ArrayBuffer — WebCrypto + Blob types reject
  // SharedArrayBuffer-backed views since DOM lib v6.
  let buf: ArrayBuffer
  if (typeof data === 'string') {
    const encoded = new TextEncoder().encode(data)
    const copy = new ArrayBuffer(encoded.byteLength)
    new Uint8Array(copy).set(encoded)
    buf = copy
  } else if (data instanceof Uint8Array) {
    const copy = new ArrayBuffer(data.byteLength)
    new Uint8Array(copy).set(data)
    buf = copy
  } else {
    buf = data
  }
  const digest = await crypto.subtle.digest('SHA-256', buf)
  return Array.from(new Uint8Array(digest))
    .map(b => b.toString(16).padStart(2, '0'))
    .join('')
}
