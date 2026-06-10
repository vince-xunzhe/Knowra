/**
 * Desktop → cloud sync agent.
 *
 * Orchestrates the 3-step push protocol from docs/SYNC-PROTOCOL.md:
 *
 *   1. prepare  — POST our staged rows + planned wiki files; server
 *                 returns signed PUT URLs for the ones it doesn't have.
 *   2. upload   — parallel signed-PUT each wiki file's bytes to Supabase
 *                 Storage. The signed URLs are pre-authorized, so we
 *                 use raw fetch (no Bearer header).
 *   3. commit   — POST sync_session_id + the rel_paths we successfully
 *                 uploaded; server HEAD-checks each file, then merges
 *                 the staged rows into canonical tables. Idempotent on
 *                 sync_session_id, so retry-after-crash is safe.
 *
 * What this module DOES NOT do (yet):
 *   - Build the LocalSnapshot — that's gatherLocalSnapshot.ts (next
 *     session; needs a backend /api/sync/local_snapshot endpoint that
 *     reshapes desktop rows into cloud-row format). For now the agent
 *     takes a snapshot in as a parameter, which keeps it testable and
 *     also lets the PipelineConsole stub call it with an empty snapshot
 *     to verify wiring end-to-end.
 *   - Per-table delete diffing — we'll pass deletions through if the
 *     caller computed them, otherwise empty.
 *   - Background polling — the agent runs when the user clicks 同步.
 *     We persist sync_session_id during the upload phase so we can
 *     resume commit on the next click after a crash.
 */

import {
  cloudCommit,
  cloudPrepare,
  cloudWarmup,
  getDeviceId,
  getStoredSession,
  performSignedUpload,
  setLastSyncAt,
  type CommitResponse,
  type KnowledgeEdgeRow,
  type KnowledgeNodeRow,
  type PaperRow,
  type PrepareResponse,
  type SyncDeletions,
  type SyncTables,
  type UploadInstruction,
  type WikiFileRow,
} from '../api/cloud'

// ── snapshot the caller hands the agent ────────────────────────────────

/** One wiki file as known to the desktop: row metadata + the raw bytes
 *  we'd upload if the server says it needs them. */
export interface LocalWikiFile {
  row: WikiFileRow
  body: Uint8Array | string
}

export interface LocalSnapshot {
  papers: PaperRow[]
  knowledge_nodes: KnowledgeNodeRow[]
  knowledge_edges: KnowledgeEdgeRow[]
  wiki_files: LocalWikiFile[]
  /** IDs we know are gone locally — server tombstones them. */
  deletions: SyncDeletions
  /** Last successful sync ts (ISO). When supplied, sent to server as
   *  `since` so the response trims wiki dedup checks. */
  since?: string | null
}

export function emptySnapshot(): LocalSnapshot {
  return {
    papers: [], knowledge_nodes: [], knowledge_edges: [], wiki_files: [],
    deletions: {}, since: null,
  }
}

// ── progress callbacks ────────────────────────────────────────────────

export type SyncStage =
  | 'idle'
  | 'preparing'
  | 'uploading'
  | 'committing'
  | 'done'
  | 'error'

export interface SyncProgress {
  stage: SyncStage
  /** Files uploaded so far in this run. */
  uploadsDone: number
  /** Files we still need to upload. */
  uploadsTotal: number
  /** Files the server said it already had (dedup). */
  uploadsSkipped: number
  /** Last file we touched — surfaced for the UI ticker. */
  currentFile?: string
  /** Set when stage is 'error'. */
  error?: string
  /** Populated once stage = done. */
  commit?: CommitResponse
}

export type SyncProgressFn = (p: SyncProgress) => void

// ── resumable session bookkeeping ──────────────────────────────────────
//
// If the user closes the app mid-upload, we want to be able to commit
// what we've already PUT without re-prepare'ing (which would issue new
// session ids and rebuild signing). We park sync_session_id +
// uploads_required in localStorage and replay commit on the next run.

const LS_PENDING = 'knowra.sync.pendingSession'

interface PendingSession {
  sync_session_id: string
  expires_at: string
  uploaded: { rel_path: string; content_hash: string }[]
}

function loadPending(): PendingSession | null {
  try {
    const raw = localStorage.getItem(LS_PENDING)
    if (!raw) return null
    const parsed = JSON.parse(raw) as PendingSession
    if (Date.parse(parsed.expires_at) < Date.now()) {
      localStorage.removeItem(LS_PENDING)
      return null
    }
    return parsed
  } catch {
    return null
  }
}

function savePending(p: PendingSession | null) {
  try {
    if (p === null) localStorage.removeItem(LS_PENDING)
    else localStorage.setItem(LS_PENDING, JSON.stringify(p))
  } catch { /* noop */ }
}

// ── core runner ───────────────────────────────────────────────────────

const UPLOAD_CONCURRENCY = 4

export class SyncError extends Error {
  readonly stage: SyncStage
  constructor(message: string, stage: SyncStage) {
    super(message)
    this.name = 'SyncError'
    this.stage = stage
  }
}

/**
 * Stamps the caller's rows with `user_id` from the current cloud
 * session. We do this here (not in the gatherer) so a session swap
 * mid-sync surfaces as an auth error rather than mixing user_ids.
 */
function stampUserId(snapshot: LocalSnapshot, userId: string): {
  tables: SyncTables
  uploadIndex: Map<string, LocalWikiFile>
} {
  const tag = <T extends { user_id: string }>(rows: T[]) =>
    rows.map(r => ({ ...r, user_id: userId }))

  const wikiRows = snapshot.wiki_files.map(w => ({ ...w.row, user_id: userId }))
  const uploadIndex = new Map<string, LocalWikiFile>()
  for (const w of snapshot.wiki_files) {
    uploadIndex.set(w.row.rel_path, w)
  }

  return {
    tables: {
      papers: tag(snapshot.papers),
      knowledge_nodes: tag(snapshot.knowledge_nodes),
      knowledge_edges: tag(snapshot.knowledge_edges),
      wiki_files: wikiRows,
    },
    uploadIndex,
  }
}

async function runUploadsWithConcurrency(
  uploads: UploadInstruction[],
  index: Map<string, LocalWikiFile>,
  onProgress: (done: number, current: string) => void,
): Promise<{ uploaded: { rel_path: string; content_hash: string }[]; failures: string[] }> {
  const uploaded: { rel_path: string; content_hash: string }[] = []
  const failures: string[] = []
  let cursor = 0
  let done = 0

  const worker = async () => {
    while (cursor < uploads.length) {
      const i = cursor++
      const instr = uploads[i]
      const file = index.get(instr.rel_path)
      if (!file) {
        // Server asked for a path we don't have locally — unusual,
        // but treat as a failure rather than a silent hole.
        failures.push(instr.rel_path)
        continue
      }
      try {
        let body: Blob
        if (typeof file.body === 'string') {
          body = new Blob([file.body], { type: 'text/markdown' })
        } else {
          // Copy into a fresh ArrayBuffer so Blob accepts the BufferSource
          // (DOM lib v6 rejects SharedArrayBuffer-backed Uint8Array).
          const copy = new ArrayBuffer(file.body.byteLength)
          new Uint8Array(copy).set(file.body)
          body = new Blob([copy], { type: 'application/octet-stream' })
        }
        await performSignedUpload(instr, body)
        uploaded.push({ rel_path: instr.rel_path, content_hash: file.row.content_hash })
      } catch (err) {
        failures.push(`${instr.rel_path}: ${(err as Error).message}`)
      } finally {
        done += 1
        onProgress(done, instr.rel_path)
      }
    }
  }

  const workers = Array.from(
    { length: Math.min(UPLOAD_CONCURRENCY, uploads.length || 1) },
    worker,
  )
  await Promise.all(workers)
  return { uploaded, failures }
}

/** Run the full sync. The caller passes a built LocalSnapshot; the agent
 *  handles prepare → uploads → commit with progress callbacks. */
export async function runSync(
  snapshot: LocalSnapshot,
  onProgress: SyncProgressFn = () => {},
): Promise<CommitResponse> {
  const session = getStoredSession()
  if (!session) {
    const err = new SyncError('未登录云端账号', 'error')
    onProgress({ stage: 'error', uploadsDone: 0, uploadsTotal: 0, uploadsSkipped: 0, error: err.message })
    throw err
  }

  const { tables, uploadIndex } = stampUserId(snapshot, session.user.id)

  // Step 0: warm up the cloud machine. With min_machines_running=1
  // this is a sub-second 401, but if the platform just restarted the
  // VM (deploy / maintenance) the first hit can 503. Riding that out
  // here — before we send the big prepare payload — means the user
  // never sees a cold-start "Network Error". Non-fatal: if warmup
  // can't confirm, we still try prepare (which has its own retries).
  onProgress({ stage: 'preparing', uploadsDone: 0, uploadsTotal: 0, uploadsSkipped: 0 })
  await cloudWarmup()

  // Step 1: prepare. (Even when resuming, we re-prepare so the server
  // can stage the rows; the resume case only saves us upload bytes.)

  let prepResp: PrepareResponse
  try {
    prepResp = await cloudPrepare({
      device_id: getDeviceId(),
      since: snapshot.since ?? null,
      tables,
      deletions: snapshot.deletions,
    })
  } catch (err) {
    const message = (err as Error).message || 'prepare 失败'
    onProgress({ stage: 'error', uploadsDone: 0, uploadsTotal: 0, uploadsSkipped: 0, error: message })
    throw new SyncError(message, 'preparing')
  }

  if (prepResp.validation_errors.length > 0) {
    const message = `校验失败：${prepResp.validation_errors.map(v => `${v.table}/${v.id ?? '?'} ${v.reason}`).join('; ')}`
    onProgress({ stage: 'error', uploadsDone: 0, uploadsTotal: 0, uploadsSkipped: 0, error: message })
    throw new SyncError(message, 'preparing')
  }

  // Persist the session so a crash mid-upload doesn't strand it.
  savePending({
    sync_session_id: prepResp.sync_session_id,
    expires_at: prepResp.expires_at,
    uploaded: [],
  })

  // Step 2: upload.
  const uploadsTotal = prepResp.uploads_required.length
  const uploadsSkipped = prepResp.uploads_skipped.length
  onProgress({ stage: 'uploading', uploadsDone: 0, uploadsTotal, uploadsSkipped })

  const { uploaded, failures } = await runUploadsWithConcurrency(
    prepResp.uploads_required,
    uploadIndex,
    (done, current) => {
      onProgress({
        stage: 'uploading',
        uploadsDone: done,
        uploadsTotal,
        uploadsSkipped,
        currentFile: current,
      })
    },
  )

  // Update pending so a crash before commit can still finish via the
  // user clicking sync again.
  savePending({
    sync_session_id: prepResp.sync_session_id,
    expires_at: prepResp.expires_at,
    uploaded,
  })

  if (failures.length > 0 && uploaded.length === 0) {
    const message = `上传全部失败：${failures.slice(0, 3).join('; ')}`
    onProgress({ stage: 'error', uploadsDone: uploaded.length, uploadsTotal, uploadsSkipped, error: message })
    throw new SyncError(message, 'uploading')
  }

  // Step 3: commit.
  onProgress({ stage: 'committing', uploadsDone: uploaded.length, uploadsTotal, uploadsSkipped })

  let commitResp: CommitResponse
  try {
    commitResp = await cloudCommit({
      sync_session_id: prepResp.sync_session_id,
      uploaded,
    })
  } catch (err) {
    const message = (err as Error).message || 'commit 失败'
    onProgress({ stage: 'error', uploadsDone: uploaded.length, uploadsTotal, uploadsSkipped, error: message })
    throw new SyncError(message, 'committing')
  }

  // A revision-0 commit means the server applied NOTHING and returned a
  // rejection list (e.g. an uploaded wiki file failed its storage
  // HEAD-check, which aborts the whole commit). The success path always
  // bumps revision to >= 1, so revision 0 is unambiguously a no-op abort.
  // Treat it as a failure: do NOT clear pending / stamp lastSync, and
  // surface the reasons so it doesn't masquerade as "✓ 已同步".
  if (commitResp.revision === 0) {
    const rj = commitResp.rejected || []
    const head = rj
      .slice(0, 3)
      .map(r => `${r.rel_path || r.table}（${r.code}）：${r.reason}`)
      .join('；')
    const more = rj.length > 3 ? ` 等共 ${rj.length} 项` : ''
    const message = rj.length > 0
      ? `云端拒收，未写入任何数据：${head}${more}`
      : '云端未写入任何数据（revision 0）'
    onProgress({ stage: 'error', uploadsDone: uploaded.length, uploadsTotal, uploadsSkipped, error: message })
    throw new SyncError(message, 'committing')
  }

  // Success: clear resumable state, stamp last-sync wall clock.
  savePending(null)
  setLastSyncAt(commitResp.server_now)

  onProgress({
    stage: 'done',
    uploadsDone: uploaded.length,
    uploadsTotal,
    uploadsSkipped,
    commit: commitResp,
  })

  return commitResp
}

// ── resume helper for the PipelineConsole UI ──────────────────────────

export interface PendingResumeInfo {
  sync_session_id: string
  expires_at: string
  uploaded_count: number
}

export function getPendingResume(): PendingResumeInfo | null {
  const p = loadPending()
  if (!p) return null
  return {
    sync_session_id: p.sync_session_id,
    expires_at: p.expires_at,
    uploaded_count: p.uploaded.length,
  }
}

/** Replay just the commit step using the cached session. Useful when
 *  upload finished but commit failed (server hiccup) — saves re-running
 *  prepare + uploads. Returns null if there's nothing to resume. */
export async function resumeCommit(
  onProgress: SyncProgressFn = () => {},
): Promise<CommitResponse | null> {
  const pending = loadPending()
  if (!pending) return null

  onProgress({
    stage: 'committing',
    uploadsDone: pending.uploaded.length,
    uploadsTotal: pending.uploaded.length,
    uploadsSkipped: 0,
  })

  try {
    const commitResp = await cloudCommit({
      sync_session_id: pending.sync_session_id,
      uploaded: pending.uploaded,
    })
    savePending(null)
    setLastSyncAt(commitResp.server_now)
    onProgress({
      stage: 'done',
      uploadsDone: pending.uploaded.length,
      uploadsTotal: pending.uploaded.length,
      uploadsSkipped: 0,
      commit: commitResp,
    })
    return commitResp
  } catch (err) {
    const message = (err as Error).message || 'resume commit 失败'
    onProgress({
      stage: 'error',
      uploadsDone: pending.uploaded.length,
      uploadsTotal: pending.uploaded.length,
      uploadsSkipped: 0,
      error: message,
    })
    throw new SyncError(message, 'committing')
  }
}
