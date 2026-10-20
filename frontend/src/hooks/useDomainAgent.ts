import { useCallback, useEffect, useRef, useState } from 'react'
import axios from 'axios'
import { listDomainRuns, type DomainAgentRun } from '../api/domains'

export function useDomainAgent(loadRuns = listDomainRuns) {
  const [runs, setRuns] = useState<DomainAgentRun[]>([])
  const [ready, setReady] = useState(false)
  const [error, setError] = useState('')
  const clearError = useCallback(() => setError(''), [])
  const [loadError, setLoadError] = useState('')
  const fetching = useRef(false)
  const [busy, setBusy] = useState(false)
  const generation = useRef(0)
  const running = useRef(false)
  const refresh = useCallback(async () => {
    if (running.current || fetching.current) return
    fetching.current = true
    const version = ++generation.current
    try {
      const next = await loadRuns()
      if (!Array.isArray(next)) throw new Error('无法确认画布编辑状态，请重试')
      if (version !== generation.current) return
      setRuns(next)
      setReady(true)
      setLoadError('')
    } catch (e) {
      if (version === generation.current) setLoadError(e instanceof Error ? e.message : String(e))
    } finally { fetching.current = false }
  }, [loadRuns])
  const act = useCallback(async (operation: () => Promise<DomainAgentRun | void>) => {
    if (running.current) return
    running.current = true
    generation.current++
    setBusy(true)
    setError('')
    try {
      const run = await operation()
      if (run) setRuns(old => [...old.filter(r => r.boardId !== run.boardId), run])
    } catch (e) {
      setError(axios.isAxiosError(e) && typeof e.response?.data?.detail === 'string'
        ? e.response.data.detail : e instanceof Error ? e.message : String(e))
    } finally {
      generation.current++
      running.current = false
      setBusy(false)
    }
  }, [])
  useEffect(() => {
    const start = setTimeout(() => void refresh(), 0)
    const timer = setInterval(() => void refresh(), 2500)
    const focus = () => void refresh()
    window.addEventListener('focus', focus)
    return () => { clearTimeout(start); clearInterval(timer); window.removeEventListener('focus', focus) }
  }, [refresh])
  return { runs, ready, error: error || loadError, busy, act, refresh, clearError }
}
