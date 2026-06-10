/**
 * React hook + context for the cloud session.
 *
 * Mounted once at App root. Wraps the imperative cloud API in
 * api/cloud.ts so any component can `const { user, login, ... } = useCloudAuth()`
 * without re-implementing localStorage reads. State is intentionally
 * minimal — we don't load /api/cloud/me here; pages that need stats
 * call it on their own to avoid a sync on every page mount.
 */
import { createContext, createElement, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

import {
  CloudAuthError,
  cloudRefreshSession,
  cloudSignIn,
  cloudSignOut,
  cloudSignUp,
  getCloudConfig,
  getStoredSession,
  isCloudConfigured,
  setCloudConfig,
  type CloudConfig,
  type CloudSession,
  type CloudUser,
} from '../api/cloud'

interface CloudAuthState {
  configured: boolean
  config: CloudConfig
  session: CloudSession | null
  user: CloudUser | null
  signingIn: boolean
  signingUp: boolean
  error: string | null
}

interface CloudAuthApi extends CloudAuthState {
  updateConfig: (next: Partial<CloudConfig>) => void
  signIn: (email: string, password: string) => Promise<CloudSession>
  signUp: (email: string, password: string) => Promise<CloudSession | null>
  signOut: () => Promise<void>
  clearError: () => void
  /** Re-read session from storage — useful after another tab logs in. */
  refresh: () => void
}

const CloudAuthContext = createContext<CloudAuthApi | null>(null)

function readState(): CloudAuthState {
  const session = getStoredSession()
  return {
    configured: isCloudConfigured(),
    config: getCloudConfig(),
    session,
    user: session?.user ?? null,
    signingIn: false,
    signingUp: false,
    error: null,
  }
}

export function CloudAuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<CloudAuthState>(readState)

  // Cross-tab sync: if another window logs in/out, mirror it here.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (!e.key) return
      if (e.key.startsWith('knowra.cloud.')) {
        setState(readState())
      }
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [])

  // Opportunistic background refresh: if the stored access token is
  // within 5 minutes of expiry, refresh it now so the next /api/sync
  // call doesn't pay the refresh latency. Best-effort.
  useEffect(() => {
    const session = state.session
    if (!session) return
    const msUntilExpiry = session.expires_at - Date.now()
    if (msUntilExpiry < 5 * 60_000 && msUntilExpiry > 0) {
      cloudRefreshSession(session.refresh_token)
        .then(fresh => setState(s => ({ ...s, session: fresh, user: fresh.user })))
        .catch(() => {
          // Refresh failed (rotated key, server down): leave session as
          // is. The next authed call's interceptor will surface the error.
        })
    }
  }, [state.session])

  const updateConfig = useCallback((next: Partial<CloudConfig>) => {
    setCloudConfig(next)
    setState(s => ({ ...s, config: getCloudConfig(), configured: isCloudConfigured() }))
  }, [])

  const clearError = useCallback(() => setState(s => ({ ...s, error: null })), [])

  const refresh = useCallback(() => setState(readState()), [])

  const signIn = useCallback(async (email: string, password: string) => {
    setState(s => ({ ...s, signingIn: true, error: null }))
    try {
      const session = await cloudSignIn(email, password)
      setState(s => ({ ...s, signingIn: false, session, user: session.user }))
      return session
    } catch (err) {
      const message = err instanceof CloudAuthError ? err.message : String(err)
      setState(s => ({ ...s, signingIn: false, error: message }))
      throw err
    }
  }, [])

  const signUp = useCallback(async (email: string, password: string) => {
    setState(s => ({ ...s, signingUp: true, error: null }))
    try {
      const session = await cloudSignUp(email, password)
      // If email confirmation required, session is null — caller will
      // tell the user to check their inbox.
      setState(s => ({
        ...s,
        signingUp: false,
        session: session ?? s.session,
        user: session?.user ?? s.user,
      }))
      return session
    } catch (err) {
      const message = err instanceof CloudAuthError ? err.message : String(err)
      setState(s => ({ ...s, signingUp: false, error: message }))
      throw err
    }
  }, [])

  const signOut = useCallback(async () => {
    await cloudSignOut()
    setState(s => ({ ...s, session: null, user: null }))
  }, [])

  const api = useMemo<CloudAuthApi>(
    () => ({ ...state, updateConfig, signIn, signUp, signOut, clearError, refresh }),
    [state, updateConfig, signIn, signUp, signOut, clearError, refresh],
  )

  return createElement(CloudAuthContext.Provider, { value: api }, children)
}

export function useCloudAuth(): CloudAuthApi {
  const ctx = useContext(CloudAuthContext)
  if (!ctx) {
    throw new Error('useCloudAuth must be used inside <CloudAuthProvider>')
  }
  return ctx
}
