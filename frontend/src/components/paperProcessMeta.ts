import { getFormattingLocale } from '../i18n/store'
import { t as tr } from '../i18n/catalog'
import type { PaperRecord } from '../api/client'

export interface ProcessStatusHint {
  running: boolean
  current: string
}

export type PaperProcessStage = 'running' | 'processed' | 'failed' | 'pending'

export interface PaperProcessMeta {
  stage: PaperProcessStage
  label: string
  summary: string
  errorSummary: string | null
}

const ERROR_SNIPPET_LIMIT = 150

export function summarizePaperError(error: string | null | undefined): string | null {
  if (!error) return null
  const compact = error.replace(/\s+/g, ' ').trim()
  if (!compact) return null
  return compact.length > ERROR_SNIPPET_LIMIT
    ? `${compact.slice(0, ERROR_SNIPPET_LIMIT - 1)}…`
    : compact
}

export function inferPaperProcessMeta(
  paper: PaperRecord,
  status?: ProcessStatusHint | null,
  pending = false,
): PaperProcessMeta {
  if (pending || Boolean(status?.running && status.current === paper.filename)) {
    return {
      stage: 'running',
      label: tr("处理中"),
      summary: status?.current || tr("任务已提交，等待后端完成"),
      errorSummary: null,
    }
  }
  if (paper.processed) {
    return {
      stage: 'processed',
      label: tr("已处理"),
      summary: paper.processed_at
        ? tr("完成于 {0}", { 0: new Date(paper.processed_at).toLocaleString(getFormattingLocale()) })
        : tr("处理完成，可在回顾页查看结构化结果"),
      errorSummary: null,
    }
  }
  if (paper.error) {
    return {
      stage: 'failed',
      label: tr("失败"),
      summary: tr("处理失败，可直接重试或重新处理"),
      errorSummary: summarizePaperError(paper.error),
    }
  }
  return {
    stage: 'pending',
    label: tr("待处理"),
    summary: tr("尚未进入处理队列"),
    errorSummary: null,
  }
}
