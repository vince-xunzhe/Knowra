import axios from 'axios'
import { readWithRetry } from './polling'

const api = axios.create({ baseURL: '/api', timeout: 8000 })
export interface BackgroundJob {
  job_id: string
  kind: string
  channel: string
  status: 'queued' | 'running' | 'completed' | 'failed' | 'interrupted'
  progress: { label?: string; channel?: string; state?: { current?: string; done?: number; total?: number } }
  error: string | null
  result: unknown
  worker_online?: boolean
}
export const getJob = (id: string) => api.get<BackgroundJob>(`/jobs/${id}`).then(r => r.data)
export const latestJob = (channel: string) => api.get<BackgroundJob | null>('/jobs/latest', { params: { channel } }).then(r => r.data)
export const listJobs = () => api.get<BackgroundJob[]>('/jobs').then(r => r.data)
export const resumeJob = (id: string) => api.post<BackgroundJob>(`/jobs/${id}/resume`).then(r => r.data)
export const startPipeline = (requestKey: string) => api.post<BackgroundJob>('/pipeline/run', { use_llm: true }, {
  headers: { 'Idempotency-Key': requestKey },
}).then(r => r.data)

export async function waitForJob<T>(job: BackgroundJob, onProgress?: (job: BackgroundJob) => void): Promise<T> {
  for (;;) {
    onProgress?.(job)
    if (job.status === 'completed') return job.result as T
    if (job.status === 'failed' || job.status === 'interrupted') {
      throw new Error(job.error || '后台任务未完成，可在任务面板恢复。')
    }
    await new Promise(resolve => setTimeout(resolve, 1500))
    job = await readWithRetry(() => getJob(job.job_id))
  }
}

export function resolveJobResult<T>(value: T | BackgroundJob): Promise<T> {
  if (value && typeof value === 'object' && 'job_id' in value && 'status' in value) {
    return waitForJob<T>(value as BackgroundJob)
  }
  return Promise.resolve(value as T)
}
