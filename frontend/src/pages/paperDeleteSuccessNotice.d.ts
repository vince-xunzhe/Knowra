export const PAPER_DELETE_SUCCESS_NOTICE_MS: 10000

export type PaperDeleteSuccessNotice = {
  tone: 'success'
  title: string
  detail?: string
  autoDismissId: number
}

export function isCurrentDeleteSuccessNotice(
  notice: { autoDismissId?: number } | null | undefined,
  autoDismissId: number,
): boolean

export function createPaperDeleteSuccessNoticeController(options?: {
  durationMs?: number
  setTimeoutFn?: (fn: () => void, ms: number) => ReturnType<typeof setTimeout>
  clearTimeoutFn?: (id: ReturnType<typeof setTimeout>) => void
}): {
  show(
    notice: { title: string; detail?: string },
    setNotice: (notice: PaperDeleteSuccessNotice) => void,
    clearIfCurrent: (autoDismissId: number) => void,
  ): number
  dispose(): void
}
