import { useCallback, useEffect, useRef, useState } from 'react'
import axios from 'axios'
import { loadDomains, saveDomains, type DomainState } from '../api/domains'

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
  const blocked = useRef(false)

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
      if (next === current.current) return
      if (record) checkpoint()
      apply(next)
    },
    [apply, checkpoint],
  )

  const undo = useCallback(() => {
    const previous = past.current.pop()
    if (!previous || !current.current) return
    future.current.push(current.current)
    apply(previous)
    refreshHistory()
  }, [apply, refreshHistory])
  const redo = useCallback(() => {
    const next = future.current.pop()
    if (!next || !current.current) return
    past.current.push(current.current)
    apply(next)
    refreshHistory()
  }, [apply, refreshHistory])

  const reload = useCallback(async () => {
    if (busy.current) return
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
  return { state, status, history, update, checkpoint, undo, redo, retry, reload, flush }
}
