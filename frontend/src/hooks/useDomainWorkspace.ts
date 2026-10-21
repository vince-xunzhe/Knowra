import { useCallback, useEffect, useRef, useState } from 'react'
import axios from 'axios'
import { domainApi, type DomainApi, type DomainState, type DomainSnapshot, type DomainBoard } from '../api/domains'
import { PAPER_CATEGORIES_CHANGED_EVENT } from '../lib/paperCategoryEvents'

function saveFailureMessage(error: unknown): string {
  if (!axios.isAxiosError(error)) return '画布保存未完成，请重试。'
  if (error.response?.status === 409) return '画布已在其他窗口更新，请重新加载'
  if (error.response?.status === 422) {
    const detail: unknown = error.response.data?.detail
    const outdatedZoom = Array.isArray(detail) && detail.some(issue => {
      if (!issue || typeof issue !== 'object') return false
      const { loc, type, ctx, input } = issue
      return Array.isArray(loc) && loc.at(-2) === 'viewport' && loc.at(-1) === 'zoom' &&
        type === 'greater_than_equal' && typeof ctx?.ge === 'number' && ctx.ge > 0.05 &&
        typeof input === 'number' && input >= 0.05 && input < ctx.ge
    })
    return outdatedZoom
      ? '当前后端仍使用旧缩放限制，请重启本地后端后重试保存；请保留本页。'
      : '后端未接受画布数据，请保留本页并重试保存。'
  }
  if (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT') return '画布保存超时，请稍后重试。'
  if (!error.response) return '无法连接后端，请检查本地服务后重试保存。'
  return '后端暂时无法保存画布，请稍后重试。'
}

/** One mounted workspace owns the session history and serial save queue. */
export function useDomainWorkspace(api: Pick<DomainApi, 'load' | 'save'> = domainApi) {
  const [state, setState] = useState<DomainState | null>(null)
  const [status, setStatus] = useState<'loading' | 'saved' | 'pending' | 'saving' | 'error' | 'conflict'>(
    'loading',
  )
  const [saveError, setSaveError] = useState('')
  const [history, setHistory] = useState({ undo: false, redo: false })
  const current = useRef<DomainState | null>(null)
  const saved = useRef<DomainState | null>(null)
  const revision = useRef(0)
  const past = useRef<DomainState[]>([])
  const future = useRef<DomainState[]>([])
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const busy = useRef(false)
  const refreshing = useRef(false)
  const blocked = useRef<'error' | 'conflict' | null>(null)
  const failureMessage = useRef('')
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
      return replacement && JSON.stringify([board.nodes, board.edges, board.researchKeywords ?? []]) ===
        JSON.stringify([replacement.nodes, replacement.edges, replacement.researchKeywords ?? []])
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
    setSaveError('')
    try {
      // Serialize saves so a slow response never overwrites a newer edit.
      while (current.current && current.current !== saved.current) {
        const snapshot = current.current
        const result = await api.save(snapshot, revision.current)
        revision.current = result.revision
        saved.current = snapshot
      }
      failureMessage.current = ''
      setStatus('saved')
    } catch (error) {
      blocked.current = axios.isAxiosError(error) && error.response?.status === 409 ? 'conflict' : 'error'
      failureMessage.current = saveFailureMessage(error)
      setSaveError(blocked.current === 'error' ? failureMessage.current : '')
      setStatus(blocked.current)
    } finally {
      busy.current = false
    }
  }, [api])

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
      const result = await api.load()
      revision.current = result.revision
      current.current = result.state
      saved.current = result.state
      blocked.current = null
      failureMessage.current = ''
      setSaveError('')
      past.current = []
      future.current = []
      setState(result.state)
      refreshHistory()
      setStatus('saved')
    } catch {
      setStatus('error')
    }
  }, [api, refreshHistory])

  const retry = useCallback(() => {
    if (blocked.current === 'conflict') return
    if (!current.current) {
      void reload()
      return
    }
    blocked.current = null
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
      const result = await api.load()
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
  }, [api, refreshHistory])

  const withSaved = useCallback(async <T,>(operation: (snapshot: DomainSnapshot) => Promise<T>) => {
    if (remoteBusy.current) throw new Error('请等待当前操作完成')
    remoteBusy.current = true
    try {
      while (busy.current) await new Promise(resolve => setTimeout(resolve, 25))
      // A user-triggered operation may retry a failed save once. Revision
      // conflicts still require an explicit reload and never retry a write.
      if (blocked.current === 'error') blocked.current = null
      await flush()
      if (blocked.current || !current.current || current.current !== saved.current) {
        throw new Error(failureMessage.current || '请先保存或重新加载画布')
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
    const onCategoriesChanged = () => void refresh()
    window.addEventListener(PAPER_CATEGORIES_CHANGED_EVENT, onCategoriesChanged)
    return () => window.removeEventListener(PAPER_CATEGORIES_CHANGED_EVENT, onCategoriesChanged)
  }, [refresh])
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
  return { state, status, saveError, history, update, checkpoint, undo, redo, retry, reload, refresh, flush, protect, withSaved, install }
}
