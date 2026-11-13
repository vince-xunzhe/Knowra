import type { FileSearchConfirmation, PaperExtractRoute } from './client'

export type FileSearchConfirmDecision =
  | { status: 'not_required' }
  | { status: 'confirmed'; confirmation: FileSearchConfirmation }
  | { status: 'cancelled' }
  | { status: 'blocked'; message: string }

export type PaperExtractRouteInspection =
  | FileSearchConfirmDecision
  | { status: 'needs_confirmation'; route: PaperExtractRoute }

export function routeErrorMessage(route: PaperExtractRoute, fallback = '当前论文抽取路径不受支持。') {
  return route.unsupported_reason || fallback
}

export function confirmationForRoute(route: PaperExtractRoute): FileSearchConfirmation {
  return {
    acknowledged: true,
    route_fingerprint: route.route_fingerprint,
  }
}

export function decidePaperExtractRoute(route: PaperExtractRoute): PaperExtractRouteInspection {
  if (!route.supported) {
    return { status: 'blocked', message: routeErrorMessage(route) }
  }
  if (!route.requires_file_search_confirmation) {
    return { status: 'not_required' }
  }
  return { status: 'needs_confirmation', route }
}

export const CANCELLED_EXTRACTION_NOTICE = "已取消此次抽取，未上传文件，也未创建抽取任务。"
