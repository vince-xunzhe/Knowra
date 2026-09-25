import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { getWikiLintJob, getWikiLintResult, runWikiLint, type LintJobState, type LintResult } from '../api/client'

interface LintContextValue {
  job: LintJobState | null
  result: LintResult | null
  connectionError: boolean
  dismissedJobId: string | null
  dismissNotification: (jobId: string) => void
  start: (useLlm: boolean) => Promise<void>
}

const LintContext = createContext<LintContextValue | null>(null)
const DISMISSED_JOB_KEY = 'knowra.wiki-lint.dismissed-job'

export function WikiLintProvider({ children }: { children: ReactNode }) {
  const [job, setJob] = useState<LintJobState | null>(null)
  const [result, setResult] = useState<LintResult | null>(null)
  const [connectionError, setConnectionError] = useState(false)
  const [dismissedJobId, setDismissedJobId] = useState<string | null>(() => {
    try { return localStorage.getItem(DISMISSED_JOB_KEY) } catch { return null }
  })
  const dismissNotification = useCallback((jobId: string) => {
    setDismissedJobId(jobId)
    try { localStorage.setItem(DISMISSED_JOB_KEY, jobId) } catch { /* Keep session dismissal if storage is unavailable. */ }
  }, [])
  const revision = useRef(0)
  const loadedResultId = useRef<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      let running = false
      const currentRevision = revision.current
      try {
        const next = await getWikiLintJob()
        if (cancelled || currentRevision !== revision.current) return
        setJob(next)
        setConnectionError(false)
        running = next.status === 'running'
        if (next.job_id && next.job_id !== loadedResultId.current &&
            (next.status === 'completed' || next.status === 'warning')) {
          const report = await getWikiLintResult()
          if (cancelled || currentRevision !== revision.current) return
          if (report.job_id === next.job_id) {
            setResult(report.result)
            loadedResultId.current = next.job_id
          }
        }
      } catch {
        if (!cancelled && currentRevision === revision.current) setConnectionError(true)
      } finally {
        if (!cancelled) timer = setTimeout(poll, running ? 2500 : 6000)
      }
    }
    void poll()
    return () => { cancelled = true; clearTimeout(timer) }
  }, [])

  const start = async (useLlm: boolean) => {
    revision.current += 1
    const next = await runWikiLint(useLlm)
    revision.current += 1
    setJob(next)
    setResult(null)
    loadedResultId.current = null
    setConnectionError(false)
  }

  return <LintContext.Provider value={{ job, result, connectionError, dismissedJobId, dismissNotification, start }}>{children}</LintContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useWikiLint() {
  const context = useContext(LintContext)
  if (!context) throw new Error('WikiLintProvider is required')
  return context
}
