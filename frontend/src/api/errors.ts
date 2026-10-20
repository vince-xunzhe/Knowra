import axios from 'axios'

interface ValidationIssue {
  loc?: unknown[]
  msg?: string
  type?: string
}

/** Turn local API failures into messages that tell the user what to do next. */
export function localApiErrorMessage(error: unknown, fallback = '操作失败，请重试。'): string {
  if (!axios.isAxiosError(error)) return error instanceof Error ? error.message : String(error)

  const detail: unknown = error.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const issues = detail.filter((issue): issue is ValidationIssue => !!issue && typeof issue === 'object')
    if (issues.some(issue => issue.type === 'extra_forbidden')) {
      return '本地服务仍在运行旧版本，请重新启动应用后重试。'
    }
    const first = issues[0]
    if (first) {
      const field = first.loc?.filter(part => part !== 'body').join('.')
      return field && first.msg ? `请求数据未通过校验（${field}）：${first.msg}` : first.msg || fallback
    }
  }
  if (!error.response) return '无法连接本地服务，请确认应用仍在运行。'
  return error.message || fallback
}
