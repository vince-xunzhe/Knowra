/**
 * Wraps the desktop's local /api/sync/local_snapshot endpoint into a
 * LocalSnapshot the sync agent can consume.
 *
 * The backend does the heavy lifting (DB rows + file hashing + wiki
 * walk) and returns one fat JSON blob. This module just:
 *   - issues the GET against the desktop FastAPI (port 8000, same
 *     origin via Vite proxy)
 *   - decodes wiki body_b64 → Uint8Array
 *   - drops the body field once decoded so the snapshot doesn't carry
 *     it twice
 *
 * We don't stamp user_id here — that's the agent's job (it has the
 * cloud session in hand). See syncAgent.stampUserId for rationale.
 */
import axios from 'axios'

import type {
  KnowledgeEdgeRow,
  KnowledgeNodeRow,
  PaperRow,
  WikiFileRow,
} from '../api/cloud'
import type { LocalSnapshot, LocalWikiFile } from './syncAgent'

// One source of truth for the desktop backend base URL. Vite proxies
// /api → http://localhost:8000 in dev; in the Tauri build the same
// origin serves the API so a relative path works there too.
const desktop = axios.create({ baseURL: '/api', timeout: 60000 })

interface LocalWikiFileWire extends WikiFileRow {
  /** Base64-encoded raw bytes from the backend; we decode + strip. */
  body_b64?: string
}

interface LocalSnapshotWire {
  papers: PaperRow[]
  knowledge_nodes: KnowledgeNodeRow[]
  knowledge_edges: KnowledgeEdgeRow[]
  wiki_files: LocalWikiFileWire[]
  deletions: {
    papers: string[]
    knowledge_nodes: string[]
    knowledge_edges: string[]
    wiki_files: string[]
  }
  generated_at: string
  counts: {
    papers: number
    knowledge_nodes: number
    knowledge_edges: number
    wiki_files: number
  }
}

function b64ToBytes(b64: string): Uint8Array {
  // atob → binary string → Uint8Array. Browser-native, no deps. We
  // tolerate the str containing whitespace/newlines (some backends
  // pretty-print base64) by stripping first.
  const clean = b64.replace(/\s+/g, '')
  const binary = atob(clean)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
  return bytes
}

export interface GatherOptions {
  /** If supplied, the snapshot's `since` field — passed to the cloud's
   *  /prepare so it can shortcut dedup checks. We don't compute it
   *  client-side; usually the caller reads the last successful sync
   *  timestamp from localStorage (api/cloud.getLastSyncAt). */
  since?: string | null
  /** Skip downloading wiki bodies. Used when the caller only wants to
   *  preview row counts. The returned snapshot's wiki_files entries
   *  will have empty bodies, so don't pass it into runSync. */
  skipWikiBodies?: boolean
}

export async function gatherLocalSnapshot(
  opts: GatherOptions = {},
): Promise<LocalSnapshot> {
  const params: Record<string, string> = {}
  if (opts.skipWikiBodies) params.include_wiki_bodies = 'false'
  const { data } = await desktop.get<LocalSnapshotWire>(
    '/sync/local_snapshot',
    { params },
  )

  const wiki_files: LocalWikiFile[] = data.wiki_files.map(w => {
    const { body_b64, ...row } = w
    const body =
      !opts.skipWikiBodies && body_b64 != null
        ? b64ToBytes(body_b64)
        : new Uint8Array(0)
    return { row: row as WikiFileRow, body }
  })

  return {
    papers: data.papers,
    knowledge_nodes: data.knowledge_nodes,
    knowledge_edges: data.knowledge_edges,
    wiki_files,
    deletions: data.deletions,
    since: opts.since ?? null,
  }
}

/** Lightweight version that only reports the row counts — useful for a
 *  "what would sync push?" preview in the UI without downloading wiki
 *  bytes. */
export async function previewSnapshotCounts(): Promise<{
  papers: number; knowledge_nodes: number; knowledge_edges: number; wiki_files: number
}> {
  const { data } = await desktop.get<LocalSnapshotWire>(
    '/sync/local_snapshot',
    { params: { include_wiki_bodies: 'false' } },
  )
  return data.counts
}
