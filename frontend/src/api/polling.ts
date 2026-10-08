type PollFailure = { code?: string; response?: { status?: number } }

export function singleFlight<T>(read: () => Promise<T>): () => Promise<T> {
  let pending: Promise<T> | null = null
  return () => {
    if (!pending) pending = read().finally(() => { pending = null })
    return pending
  }
}

export function isTransientPollError(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false
  const { code, response } = error as PollFailure
  return ['ECONNABORTED', 'ETIMEDOUT', 'ERR_NETWORK'].includes(code ?? '')
    || [408, 429, 500, 502, 503, 504].includes(response?.status ?? 0)
}

// Retry read-only status requests, never a task submission that may have succeeded.
export async function readWithRetry<T>(
  read: () => Promise<T>,
  options: {
    onRetry?: (attempt: number) => void
    retries?: number
    sleep?: (ms: number) => Promise<void>
  } = {},
): Promise<T> {
  const sleep = options.sleep ?? (ms => new Promise(resolve => setTimeout(resolve, ms)))
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await read()
    } catch (error) {
      if (!isTransientPollError(error)) throw error
      if (attempt >= (options.retries ?? 5)) {
        throw new Error('暂时无法确认后台任务状态，自动编排已暂停；后台任务可能仍在运行，请勿重复提交。')
      }
      options.onRetry?.(attempt + 1)
      await sleep(Math.min(1500 * 2 ** attempt, 8000))
    }
  }
}
