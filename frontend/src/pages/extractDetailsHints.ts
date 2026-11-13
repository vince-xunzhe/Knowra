import { t as tr } from '../i18n/catalog'

export interface ExtractCoverageHintInput {
  strategy?: string | null
  page_iteration_complete?: boolean | null
  failed_pages?: number | null
  failed_page_numbers?: Array<number | string> | null
  local_text_fallback_used?: boolean
}

export interface SourceEvidenceHintInput {
  page_iteration_complete?: boolean | null
  failed_pages?: number | null
}

function formatFailedPages(
  failedCount?: number | null,
  failedNumbers?: Array<number | string> | null,
): string | null {
  const pages = (failedNumbers || [])
    .map(value => String(value).trim())
    .filter(Boolean)
  if (pages.length > 0) return tr("失败页 {0}", { 0: pages.join(', ') })
  if ((failedCount || 0) > 0) return tr("存在失败页")
  return null
}

/** Concise exception hints that stay visible while extraction details are collapsed. */
export function collectExtractDetailHints(input: {
  processStage?: string | null
  processError?: string | null
  coverage?: ExtractCoverageHintInput | null
  sourceEvidence?: SourceEvidenceHintInput | null
}): string[] {
  const hints: string[] = []
  if (input.processStage === 'failed') {
    const detail = (input.processError || '').trim()
    hints.push(detail ? tr("处理失败：{0}", { 0: detail }) : tr("处理失败"))
  }

  const coverageIncomplete = input.coverage?.page_iteration_complete === false
  const evidenceIncomplete = input.sourceEvidence?.page_iteration_complete === false
  if (coverageIncomplete || evidenceIncomplete) {
    hints.push(tr("原文覆盖不完整"))
  }

  const failedHint =
    formatFailedPages(input.coverage?.failed_pages, input.coverage?.failed_page_numbers)
    || formatFailedPages(input.sourceEvidence?.failed_pages, null)
  if (failedHint) hints.push(failedHint)

  if (input.coverage?.local_text_fallback_used) {
    hints.push(tr("使用了有长度上限的本地文本兜底"))
  }

  return hints
}
