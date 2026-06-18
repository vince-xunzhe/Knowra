/**
 * Knowra mobile cloud client — Supabase Auth + read endpoints.
 *
 * Mirrors `frontend/src/api/cloud.ts` so behavior is consistent across
 * desktop and mobile, but with two differences:
 *
 *   1. Storage is AsyncStorage (async API) rather than localStorage —
 *      so all our getters/setters here are async.
 *   2. We only need the *read* surface (no `cloudPrepare` / `cloudCommit`);
 *      mobile is a consumer, not a producer. Desktop pushes; mobile pulls.
 *
 * The reason we don't `@supabase/supabase-js` is the same as on desktop:
 * we only need four endpoints (sign-in, sign-up, refresh, logout) and
 * one HTTP transport (axios) we already pull in. The full SDK + GoTrue
 * client is a heavy dependency for that surface.
 */
import AsyncStorage from '@react-native-async-storage/async-storage'
import axios, { AxiosError, type AxiosInstance } from 'axios'

const LS = {
  supabaseUrl: 'knowra.cloud.supabaseUrl',
  supabaseAnonKey: 'knowra.cloud.supabaseAnonKey',
  baseUrl: 'knowra.cloud.baseUrl',
  session: 'knowra.cloud.session',
  openaiKey: 'knowra.cloud.openaiKey',  // mobile-only: user's key for Ask
} as const

export interface CloudConfig {
  supabaseUrl: string
  supabaseAnonKey: string
  baseUrl: string
}

/**
 * Baked-in production cloud config. These are PUBLIC values — the Supabase
 * project URL, its anon/publishable key, and the Fly backend URL — and are
 * safe to ship in the app bundle: per-user isolation is enforced by Supabase
 * RLS + each user's own login, NOT by hiding these (same model as Firebase's
 * apiKey). A fresh install therefore works with login only — no setup screen.
 * Power users / self-hosters can override any field in Settings → 高级.
 */
export const CLOUD_DEFAULTS: CloudConfig = {
  supabaseUrl: 'https://umflsxjvndppadtnfxke.supabase.co',
  supabaseAnonKey:
    'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InVtZmxzeGp2bmRwcGFkdG5meGtlIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA0MDkwMDEsImV4cCI6MjA5NTk4NTAwMX0.hClTx8Zt7nFCIMw62b710zx-uaFKcDHdfAkyw45pFJM',
  baseUrl: 'https://knowra-cloud.fly.dev',
}

export interface CloudUser {
  id: string
  email: string | null
  display_name: string | null
}

export interface CloudSession {
  access_token: string
  refresh_token: string
  expires_at: number // unix ms
  user: CloudUser
}

// ── config + session storage ───────────────────────────────────────────

export async function getCloudConfig(): Promise<CloudConfig> {
  const [url, key, base] = await Promise.all([
    AsyncStorage.getItem(LS.supabaseUrl),
    AsyncStorage.getItem(LS.supabaseAnonKey),
    AsyncStorage.getItem(LS.baseUrl),
  ])
  // Effective config: an explicit user override (AsyncStorage) wins per-field;
  // otherwise fall back to the baked-in CLOUD_DEFAULTS so a fresh install works
  // with login only.
  return {
    supabaseUrl: (url ?? '').trim() || CLOUD_DEFAULTS.supabaseUrl,
    supabaseAnonKey: (key ?? '').trim() || CLOUD_DEFAULTS.supabaseAnonKey,
    baseUrl: ((base ?? '').trim() || CLOUD_DEFAULTS.baseUrl).replace(/\/+$/, ''),
  }
}

/** Raw user overrides only (empty string = not set → the effective value comes
 *  from CLOUD_DEFAULTS). The Settings UI reads this so a blank field means
 *  "use the built-in default" rather than showing the default as if typed. */
export async function getCloudConfigOverride(): Promise<CloudConfig> {
  const [url, key, base] = await Promise.all([
    AsyncStorage.getItem(LS.supabaseUrl),
    AsyncStorage.getItem(LS.supabaseAnonKey),
    AsyncStorage.getItem(LS.baseUrl),
  ])
  return {
    supabaseUrl: (url ?? '').trim(),
    supabaseAnonKey: (key ?? '').trim(),
    baseUrl: (base ?? '').trim(),
  }
}

export async function setCloudConfig(next: Partial<CloudConfig>): Promise<void> {
  const writes: Promise<unknown>[] = []
  if (next.supabaseUrl !== undefined) {
    writes.push(setOrRemove(LS.supabaseUrl, next.supabaseUrl.trim()))
  }
  if (next.supabaseAnonKey !== undefined) {
    writes.push(setOrRemove(LS.supabaseAnonKey, next.supabaseAnonKey.trim()))
  }
  if (next.baseUrl !== undefined) {
    writes.push(setOrRemove(LS.baseUrl, next.baseUrl.trim().replace(/\/+$/, '')))
  }
  await Promise.all(writes)
}

async function setOrRemove(key: string, value: string): Promise<void> {
  if (!value) await AsyncStorage.removeItem(key)
  else await AsyncStorage.setItem(key, value)
}

export async function getStoredSession(): Promise<CloudSession | null> {
  const raw = await AsyncStorage.getItem(LS.session)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as CloudSession
    if (!parsed.access_token || !parsed.refresh_token) return null
    return parsed
  } catch {
    return null
  }
}

async function storeSession(session: CloudSession | null): Promise<void> {
  if (session === null) await AsyncStorage.removeItem(LS.session)
  else await AsyncStorage.setItem(LS.session, JSON.stringify(session))
}

export async function getOpenAIKey(): Promise<string> {
  return (await AsyncStorage.getItem(LS.openaiKey)) ?? ''
}

export async function setOpenAIKey(value: string): Promise<void> {
  await setOrRemove(LS.openaiKey, value.trim())
}

// ── Supabase Auth REST ─────────────────────────────────────────────────

export class CloudAuthError extends Error {
  readonly code?: string
  constructor(message: string, code?: string) {
    super(message)
    this.name = 'CloudAuthError'
    this.code = code
  }
}

async function authClient(): Promise<AxiosInstance> {
  const { supabaseUrl, supabaseAnonKey } = await getCloudConfig()
  if (!supabaseUrl || !supabaseAnonKey) {
    throw new CloudAuthError(
      '请先到设置中填写 Supabase URL 和 anon key',
      'not_configured',
    )
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

interface SupabaseAuthError {
  error?: string
  error_description?: string
  msg?: string
  message?: string
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
    const client = await authClient()
    const { data } = await client.post<SupabaseTokenResponse>(
      '/token?grant_type=password',
      { email, password },
    )
    const session = sessionFromToken(data)
    await storeSession(session)
    return session
  } catch (err) {
    throw toAuthError(err)
  }
}

export async function cloudSignUp(email: string, password: string): Promise<CloudSession | null> {
  try {
    const client = await authClient()
    const { data } = await client.post<SupabaseTokenResponse | { id: string }>(
      '/signup',
      { email, password },
    )
    if ('access_token' in data && (data as SupabaseTokenResponse).access_token) {
      const session = sessionFromToken(data as SupabaseTokenResponse)
      await storeSession(session)
      return session
    }
    return null // email confirmation required
  } catch (err) {
    throw toAuthError(err)
  }
}

export async function cloudRefreshSession(refreshToken: string): Promise<CloudSession> {
  try {
    const client = await authClient()
    const { data } = await client.post<SupabaseTokenResponse>(
      '/token?grant_type=refresh_token',
      { refresh_token: refreshToken },
    )
    const session = sessionFromToken(data)
    await storeSession(session)
    return session
  } catch (err) {
    throw toAuthError(err)
  }
}

export async function cloudSignOut(): Promise<void> {
  const session = await getStoredSession()
  await storeSession(null)
  if (!session) return
  try {
    const client = await authClient()
    await client.post('/logout', undefined, {
      headers: { Authorization: `Bearer ${session.access_token}` },
    })
  } catch {
    // Best-effort; local token is already cleared.
  }
}

async function getFreshAccessToken(bufferMs = 60_000): Promise<string> {
  const session = await getStoredSession()
  if (!session) throw new CloudAuthError('未登录', 'no_session')
  if (Date.now() < session.expires_at - bufferMs) return session.access_token
  const refreshed = await cloudRefreshSession(session.refresh_token)
  return refreshed.access_token
}

// ── Knowra cloud read client ───────────────────────────────────────────

async function cloudClient(): Promise<AxiosInstance> {
  const { baseUrl } = await getCloudConfig()
  if (!baseUrl) {
    throw new CloudAuthError('请先在设置中填写云后端 URL', 'not_configured')
  }
  const token = await getFreshAccessToken()
  return axios.create({
    baseURL: baseUrl.replace(/\/+$/, ''),
    timeout: 60000,
    headers: { Authorization: `Bearer ${token}` },
  })
}

// ── response shapes (mirror backend/schemas/cloud.py) ─────────────────

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

export interface PaperRow {
  id: string
  user_id: string
  title?: string | null
  filename?: string
  authors?: unknown[] | null
  num_pages?: number | null
  processed?: boolean | null
  processed_at?: string | null
  paper_category_model?: string | null
  paper_category_override?: string | null
  paper_team_model?: string | null
  paper_team_override?: string | null
  raw_llm_response?: string | null
  notes?: string | null
  updated_at?: string | null
  [key: string]: unknown
}

export interface KnowledgeNodeRow {
  id: string
  user_id: string
  title: string
  content: string
  node_type?: string | null
  promotion_status?: string | null
  tags?: unknown[] | null
  source_paper_ids?: string[] | null
  updated_at?: string | null
  [key: string]: unknown
}

export interface WikiFileEntry {
  id: string
  user_id: string
  kind: string
  rel_path: string
  content_hash: string
  size_bytes: number
  title?: string | null
  paper_id?: string | null
  concept_id?: string | null
  updated_at?: string | null
  download_url: string
  download_url_expires_at: string
}

export interface SnapshotResponse {
  revision: number
  server_now: string
  papers: PaperRow[]
  knowledge_nodes: KnowledgeNodeRow[]
  knowledge_edges: unknown[]
  wiki_files: WikiFileEntry[]
  deleted_since: {
    papers: string[]
    knowledge_nodes: string[]
    knowledge_edges: string[]
    wiki_files: string[]
  }
}

export interface WikiSearchHit {
  id: string
  kind: string
  rel_path: string
  title?: string | null
  snippet?: string | null
}

export interface AskCitation {
  kind: string
  ref: string
  file_id?: string | null
  rel_path?: string | null
  title?: string | null
}

export interface AskResponse {
  answer: string
  citations: AskCitation[]
  trace: { step: number; name: string; summary: string; duration_ms: number }[]
  tokens: { prompt: number; completion: number; total: number }
  model: string
}

// ── endpoint wrappers ─────────────────────────────────────────────────

export async function cloudMe(): Promise<MeResponse> {
  const c = await cloudClient()
  const { data } = await c.get<MeResponse>('/api/cloud/me')
  return data
}

export async function cloudSnapshot(since?: string): Promise<SnapshotResponse> {
  const c = await cloudClient()
  const { data } = await c.get<SnapshotResponse>('/api/cloud/snapshot', {
    params: since ? { since } : undefined,
  })
  return data
}

/** Fetch the raw markdown for a wiki file via its signed download URL.
 *  The URL is pre-signed so we don't send our Bearer token. */
export async function fetchWikiBody(downloadUrl: string): Promise<string> {
  const res = await axios.get<string>(downloadUrl, {
    transformResponse: [data => String(data)],
    responseType: 'text',
    timeout: 30000,
  })
  return res.data
}

/** Fetch a wiki file's markdown by its file id, getting a FRESH signed
 *  URL server-side each time. Robust against the snapshot's signed URLs
 *  expiring (10-min TTL) — we always mint a new one at view time.
 *
 *  Uses `fetch` (not the axios cloud client) so the 302 redirect to
 *  Supabase Storage is followed WITHOUT forwarding our Bearer token
 *  cross-origin (fetch strips Authorization on cross-origin redirects;
 *  Supabase's signed URL must not receive our JWT). */
export async function fetchWikiById(fileId: string): Promise<string> {
  const { baseUrl } = await getCloudConfig()
  if (!baseUrl) throw new CloudAuthError('未配置云后端 URL', 'not_configured')
  const token = await getFreshAccessToken()
  const res = await fetch(
    `${baseUrl.replace(/\/+$/, '')}/api/cloud/wiki/${encodeURIComponent(fileId)}`,
    { headers: { Authorization: `Bearer ${token}` }, redirect: 'follow' },
  )
  if (!res.ok) {
    throw new Error(`wiki fetch failed ${res.status}`)
  }
  return res.text()
}

export async function cloudWikiSearch(q: string, limit = 20): Promise<WikiSearchHit[]> {
  const c = await cloudClient()
  const { data } = await c.post<{ query: string; hits: WikiSearchHit[] }>(
    '/api/cloud/wiki/search',
    { q, limit },
  )
  return data.hits
}

export async function cloudAsk(
  question: string,
  openaiKey: string,
  history: { role: 'user' | 'assistant'; content: string }[] = [],
  model?: string,
): Promise<AskResponse> {
  const c = await cloudClient()
  const { data } = await c.post<AskResponse>(
    '/api/cloud/ask',
    {
      question,
      openai_api_key: openaiKey,
      history,
      ...(model ? { model } : {}),
    },
    { timeout: 600000 },
  )
  return data
}

// ── recommendations (read + 收藏 marks) ────────────────────────────────

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
  summary: string | null // desktop-generated; mobile shows it if present
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

export async function cloudRecommendations(days = 7): Promise<RecommendationsResponse> {
  const c = await cloudClient()
  const { data } = await c.get<RecommendationsResponse>('/api/cloud/recommendations', {
    params: { days },
  })
  return data
}

export async function cloudRecMarks(): Promise<string[]> {
  const c = await cloudClient()
  const { data } = await c.get<{ arxiv_ids: string[] }>('/api/cloud/rec-marks')
  return data.arxiv_ids
}

export async function cloudAddRecMark(arxivId: string): Promise<void> {
  const c = await cloudClient()
  await c.post('/api/cloud/rec-marks', { arxiv_id: arxivId })
}

export async function cloudRemoveRecMark(arxivId: string): Promise<void> {
  const c = await cloudClient()
  await c.delete(`/api/cloud/rec-marks/${encodeURIComponent(arxivId)}`)
}
