import { useCallback, useEffect, useRef, useState } from 'react'
import axios from 'axios'
import { getRoadmapImport, startRoadmapImport, type RoadmapImportJob } from '../api/roadmaps'

export function useRoadmapImport(boardId: string | null, active: boolean, refreshPapers: () => Promise<void>) {
  const [jobs, setJobs] = useState<Record<string, RoadmapImportJob | null>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [loadErrors, setLoadErrors] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState<string | null>(null)
  const pending = useRef<string | null>(null)
  const versions = useRef(new Map<string, number>())
  const signatures = useRef(new Map<string, string>())
  const load = useCallback(async (id: string, stopped: () => boolean = () => false) => {
    if (pending.current === id) return
    const version = versions.current.get(id) ?? 0
    try {
      const job = await getRoadmapImport(id)
      if (stopped() || version !== (versions.current.get(id) ?? 0)) return
      setJobs(previous => ({ ...previous, [id]: job }))
      setLoadErrors(previous => ({ ...previous, [id]: '' }))
      const signature = JSON.stringify(job)
      if (signatures.current.get(id) !== signature) {
        signatures.current.set(id, signature)
        void refreshPapers()
      }
    } catch (error) {
      if (!stopped() && version === (versions.current.get(id) ?? 0)) {
        setLoadErrors(previous => ({ ...previous, [id]: error instanceof Error ? error.message : String(error) }))
      }
    }
  }, [refreshPapers])
  useEffect(() => {
    if (!active || !boardId) return
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    const poll = async () => {
      await load(boardId, () => stopped)
      if (!stopped) timer = setTimeout(() => void poll(), 2000)
    }
    void poll()
    return () => { stopped = true; clearTimeout(timer) }
  }, [active, boardId, load])
  const start = useCallback(async (id: string, revision: number) => {
    if (pending.current) return
    pending.current = id
    setSubmitting(id)
    versions.current.set(id, (versions.current.get(id) ?? 0) + 1)
    setErrors(previous => ({ ...previous, [id]: '' }))
    try {
      const job = await startRoadmapImport(id, revision)
      setJobs(previous => ({ ...previous, [id]: job }))
      signatures.current.set(id, JSON.stringify(job))
      void refreshPapers()
    } catch (error) {
      setErrors(previous => ({ ...previous, [id]: axios.isAxiosError(error) && typeof error.response?.data?.detail === 'string'
        ? error.response.data.detail : error instanceof Error ? error.message : String(error) }))
    } finally { pending.current = null; setSubmitting(null) }
  }, [refreshPapers])
  const job = boardId ? jobs[boardId] : undefined
  return {
    job, start,
    ready: !!boardId && boardId in jobs,
    busy: !!submitting || job?.status === 'queued' || job?.status === 'running',
    error: boardId ? errors[boardId] || loadErrors[boardId] || '' : '',
    refresh: () => {
      if (!boardId) return
      setErrors(previous => ({ ...previous, [boardId]: '' }))
      void load(boardId)
    },
  }
}
