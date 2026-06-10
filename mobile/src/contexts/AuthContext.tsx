/**
 * Cloud auth context for the mobile app.
 *
 * Mounted once at the root. Tracks the Supabase session + the user's
 * cloud config (URL + anon key + backend URL) and exposes
 * `signIn` / `signUp` / `signOut` for screens to call.
 *
 * Boot semantics: on mount we read AsyncStorage once and surface a
 * `loading=true` until that resolves so the gate component can pick
 * the right initial screen (login vs tabs) without flicker.
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

import {
  cloudSignIn,
  cloudSignOut,
  cloudSignUp,
  getCloudConfig,
  getStoredSession,
  setCloudConfig as persistCloudConfig,
  type CloudConfig,
  type CloudSession,
  type CloudUser,
} from '../api/cloud'

interface AuthState {
  loading: boolean
  configured: boolean
  config: CloudConfig
  session: CloudSession | null
  user: CloudUser | null
  signingIn: boolean
  error: string | null
}

interface AuthApi extends AuthState {
  updateConfig: (next: Partial<CloudConfig>) => Promise<void>
  signIn: (email: string, password: string) => Promise<void>
  signUp: (email: string, password: string) => Promise<CloudSession | null>
  signOut: () => Promise<void>
  clearError: () => void
}

const AuthContext = createContext<AuthApi | null>(null)

const EMPTY_CONFIG: CloudConfig = { supabaseUrl: '', supabaseAnonKey: '', baseUrl: '' }

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>({
    loading: true,
    configured: false,
    config: EMPTY_CONFIG,
    session: null,
    user: null,
    signingIn: false,
    error: null,
  })

  // Boot: hydrate from AsyncStorage exactly once.
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const [config, session] = await Promise.all([getCloudConfig(), getStoredSession()])
      if (cancelled) return
      setState(s => ({
        ...s,
        loading: false,
        config,
        configured: Boolean(config.supabaseUrl && config.supabaseAnonKey && config.baseUrl),
        session,
        user: session?.user ?? null,
      }))
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const updateConfig = useCallback(async (next: Partial<CloudConfig>) => {
    await persistCloudConfig(next)
    const fresh = await getCloudConfig()
    setState(s => ({
      ...s,
      config: fresh,
      configured: Boolean(fresh.supabaseUrl && fresh.supabaseAnonKey && fresh.baseUrl),
    }))
  }, [])

  const signIn = useCallback(async (email: string, password: string) => {
    setState(s => ({ ...s, signingIn: true, error: null }))
    try {
      const session = await cloudSignIn(email, password)
      setState(s => ({ ...s, signingIn: false, session, user: session.user }))
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      setState(s => ({ ...s, signingIn: false, error: message }))
      throw err
    }
  }, [])

  const signUp = useCallback(async (email: string, password: string) => {
    setState(s => ({ ...s, signingIn: true, error: null }))
    try {
      const session = await cloudSignUp(email, password)
      setState(s => ({
        ...s,
        signingIn: false,
        session: session ?? s.session,
        user: session?.user ?? s.user,
      }))
      return session
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      setState(s => ({ ...s, signingIn: false, error: message }))
      throw err
    }
  }, [])

  const signOut = useCallback(async () => {
    await cloudSignOut()
    setState(s => ({ ...s, session: null, user: null }))
  }, [])

  const clearError = useCallback(() => setState(s => ({ ...s, error: null })), [])

  const api = useMemo<AuthApi>(
    () => ({ ...state, updateConfig, signIn, signUp, signOut, clearError }),
    [state, updateConfig, signIn, signUp, signOut, clearError],
  )

  return <AuthContext.Provider value={api}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthApi {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
