import { useCallback, useEffect, useRef, useState } from 'react'
import axios from 'axios'
import { loadDomains, saveDomains, type DomainState, type DomainSnapshot, type DomainBoard } from '../api/domains'

/** One mounted workspace owns the session history and serial save queue. */
export function useDomainWorkspace() {
  const [state, setState] = useState<DomainState | null>(null)
  const [status, setStatus] = useState<'loading' | 'saved' | 'pending' | 'saving' | 'error' | 'conflict'>(
    'loading',
  )
  const [history, setHistory] = useState({ undo: false, redo: false })
  const current = useRef<DomainState | null>(null)
  const saved = useRef<DomainState | null>(null)
  const revision = useRef(0)
  const past = useRef<DomainState[]>([])
  const future = useRef<DomainState[]>([])
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const busy = useRef(false)
  const refreshing = useRef(false)
  const blocked = useRef(false)
  const protectedBoards = useRef<Set<string> | null>(null)
  const remoteBusy = useRef(false)
  const protect = useCallback((ids: string[] | null) => {
    protectedBoards.current = ids === null ? null : new Set(ids)
  }, [])
  const permits = useCallback((next: DomainState) => {
    if (remoteBusy.current) return false
    return current.current?.boards.every((board) => {
      if (protectedBoards.current && !protectedBoards.current.has(board.id)) return true
      const replacement = next.boards.find(b => b.id === board.id)
      return replacement && JSON.stringify([board.nodes, board.edges]) === JSON.stringify([replacement.nodes, replacement.edges])
    }) ?? true
  }, [])

  const refreshHistory = useCallback(
    () => setHistory({ undo: past.current.length > 0, redo: future.current.length > 0 }),
    [],
  )
  const flush = useCallback(async () => {
    if (busy.current || blocked.current || !current.current || current.current === saved.current) return
    busy.current = true
    setStatus('saving')
    try {
      // Serialize saves so a slow response never overwrites a newer edit.
      while (current.current && current.current !== saved.current) {
        const snapshot = current.current
        const result = await saveDomains(snapshot, revision.current)
        revision.current = result.revision
        saved.current = snapshot
      }
      setStatus('saved')
    } catch (error) {
      blocked.current = true
      setStatus(axios.isAxiosError(error) && error.response?.status === 409 ? 'conflict' : 'error')
    } finally {
      busy.current = false
    }
  }, [])

  const apply = useCallback(
    (next: DomainState) => {
      current.current = next
      setState(next)
      if (!blocked.current) setStatus('pending')
      if (timer.current) clearTimeout(timer.current)
      timer.current = setTimeout(() => void flush(), 650)
    },
    [flush],
  )

  const checkpoint = useCallback(() => {
    if (!current.current) return
    past.current = [...past.current.slice(-99), current.current]
    future.current = []
    refreshHistory()
  }, [refreshHistory])

  const update = useCallback(
    (fn: (s: DomainState) => DomainState, record = true) => {
      if (!current.current) return
      const next = fn(current.current)
      if (next === current.current || !permits(next)) return
      if (record) checkpoint()
      apply(next)
    },
    [apply, checkpoint, permits],
  )

  const undo = useCallback(() => {
    const previous = past.current.at(-1)
    if (!previous || !current.current || !permits(previous)) return
    past.current.pop()
    future.current.push(current.current)
    apply(previous)
    refreshHistory()
  }, [apply, refreshHistory, permits])
  const redo = useCallback(() => {
    const next = future.current.at(-1)
    if (!next || !current.current || !permits(next)) return
    future.current.pop()
    past.current.push(current.current)
    apply(next)
    refreshHistory()
  }, [apply, refreshHistory, permits])

  const reload = useCallback(async () => {
    if (busy.current || remoteBusy.current) return
    if (timer.current) clearTimeout(timer.current)
    setStatus('loading')
    try {
      const result = await loadDomains()
      revision.current = result.revision
      current.current = result.state
      saved.current = result.state
      blocked.current = false
      past.current = []
      future.current = []
      setState(result.state)
      refreshHistory()
      setStatus('saved')
    } catch {
      setStatus('error')
    }
  }, [refreshHistory])

  const retry = useCallback(() => {
    if (!current.current) {
      void reload()
      return
    }
    blocked.current = false
    void flush()
  }, [flush, reload])

  const refresh = useCallback(async () => {
    const snapshot = current.current
    const version = revision.current
    if (
      !snapshot || snapshot !== saved.current || busy.current || remoteBusy.current || blocked.current || refreshing.current
    ) return
    refreshing.current = true
    try {
      const result = await loadDomains()
      // A taxonomy refresh must never replace edits made while the request ran.
      if (
        current.current !== snapshot || busy.current || remoteBusy.current || blocked.current || revision.current !== version
      ) return
      if (result.revision === version) return
      revision.current = result.revision
      current.current = saved.current = result.state
      past.current = []
      future.current = []
      setState(result.state)
      refreshHistory()
    } catch {
      // Keep the current canvas usable; the next entry/focus refresh retries.
    } finally {
      refreshing.current = false
    }
  }, [refreshHistory])

  const withSaved = useCallback(async <T,>(operation: (snapshot: DomainSnapshot) => Promise<T>) => {
    if (remoteBusy.current) throw new Error('请等待当前操作完成')
    remoteBusy.current = true
    try {
      while (busy.current) await new Promise(resolve => setTimeout(resolve, 25))
      await flush()
      if (blocked.current || !current.current || current.current !== saved.current) {
        throw new Error('请先保存或重新加载画布')
      }
      return await operation({ state: current.current, revision: revision.current })
    } finally { remoteBusy.current = false }
  }, [flush])

  const install = useCallback((snapshot: DomainSnapshot & { before: DomainBoard }) => {
    const beforeState = { ...snapshot.state, boards: snapshot.state.boards.map(b => b.id === snapshot.before.id ? snapshot.before : b) }
    past.current = [...(snapshot.revision === revision.current + 1 ? past.current.slice(-99) : []), beforeState]
    future.current = []
    revision.current = snapshot.revision
    current.current = saved.current = snapshot.state
    setState(snapshot.state)
    setStatus('saved')
    refreshHistory()
  }, [refreshHistory])

  useEffect(() => {
    const start = setTimeout(() => void reload(), 0)
    return () => clearTimeout(start)
  }, [reload])
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (current.current !== saved.current) {
        event.preventDefault()
        event.returnValue = ''
      }
    }
    const onVisibility = () => {
      if (document.visibilityState === 'hidden') void flush()
    }
    window.addEventListener('beforeunload', beforeUnload)
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      if (timer.current) clearTimeout(timer.current)
      window.removeEventListener('beforeunload', beforeUnload)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [flush])
  return { state, status, history, update, checkpoint, undo, redo, retry, reload, refresh, flush, protect, withSaved, install }
}
