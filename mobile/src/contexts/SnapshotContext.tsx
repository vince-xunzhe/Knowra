/**
 * Snapshot context — holds the latest cloud snapshot in memory so the
 * Papers / Concepts / Wiki screens can all read from the same source
 * without each spinning their own fetch on mount.
 *
 * Snapshot is fetched lazily on first access and refreshable from any
 * screen via `refresh()`. Pull-to-refresh on a list calls it; navigating
 * does not (the data isn't moving fast enough to justify the latency).
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

import { cloudSnapshot, type SnapshotResponse } from '../api/cloud'
import { useAuth } from './AuthContext'

interface SnapshotState {
  loading: boolean
  data: SnapshotResponse | null
  error: string | null
  fetchedAt: number | null
}

interface SnapshotApi extends SnapshotState {
  refresh: () => Promise<void>
}

const Ctx = createContext<SnapshotApi | null>(null)

export function SnapshotProvider({ children }: { children: React.ReactNode }) {
  const auth = useAuth()
  const [state, setState] = useState<SnapshotState>({
    loading: false, data: null, error: null, fetchedAt: null,
  })

  const refresh = useCallback(async () => {
    if (!auth.user) return
    setState(s => ({ ...s, loading: true, error: null }))
    try {
      const data = await cloudSnapshot()
      setState({ loading: false, data, error: null, fetchedAt: Date.now() })
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      setState(s => ({ ...s, loading: false, error: message }))
    }
  }, [auth.user])

  // Auto-fetch when user logs in.
  useEffect(() => {
    if (auth.user && state.data === null && !state.loading) {
      void refresh()
    }
    // Clear cached data on logout so the next user doesn't see the
    // previous account's snapshot.
    if (!auth.user && state.data !== null) {
      setState({ loading: false, data: null, error: null, fetchedAt: null })
    }
  }, [auth.user, state.data, state.loading, refresh])

  const api = useMemo<SnapshotApi>(() => ({ ...state, refresh }), [state, refresh])
  return <Ctx.Provider value={api}>{children}</Ctx.Provider>
}

export function useSnapshot(): SnapshotApi {
  const v = useContext(Ctx)
  if (!v) throw new Error('useSnapshot must be used inside <SnapshotProvider>')
  return v
}
