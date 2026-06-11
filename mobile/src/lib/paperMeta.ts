/**
 * Shared paper-field derivation, used by the Papers list and the Home
 * dashboard so category/year logic stays in one place (mirrors the desktop
 * 编译图谱 lanes + extraction JSON shape).
 */
import type { PaperRow } from '../api/cloud'

// Same lane order as the desktop 编译图谱
// (backend paper_category_service.PAPER_CATEGORY_OPTIONS).
export const CATEGORY_ORDER = [
  'LLM', 'VLM', 'VLA', '三维重建-静态', '三维重建-动态', '世界模型', '其他',
]

export function categoryOf(p: PaperRow): string {
  // Mirror backend effective_paper_category precedence:
  // human override → model prediction → raw extraction → 其他.
  const override = p.paper_category_override as string | undefined
  const model = p.paper_category_model as string | undefined
  if (override) return override
  if (model) return model
  const raw = p.raw_llm_response as string | undefined
  if (raw) {
    try {
      const c = (JSON.parse(raw) as { paper_category?: string }).paper_category
      if (c) return c
    } catch { /* not JSON — fall through */ }
  }
  return '其他'
}

export function categoryRank(name: string): number {
  const i = CATEGORY_ORDER.indexOf(name)
  return i === -1 ? CATEGORY_ORDER.length : i
}

// Publication year, read from the extraction JSON. 0 = unknown.
export function paperYear(p: PaperRow): number {
  const raw = p.raw_llm_response as string | undefined
  if (raw) {
    try {
      const y = (JSON.parse(raw) as { year?: string | number }).year
      const n = parseInt(String(y ?? ''), 10)
      if (!Number.isNaN(n) && n > 1900) return n
    } catch { /* not JSON — fall through */ }
  }
  return 0
}
