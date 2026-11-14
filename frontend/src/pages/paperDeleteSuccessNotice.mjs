/** Auto-dismisses only the library delete-success notice after a fixed delay. */
export const PAPER_DELETE_SUCCESS_NOTICE_MS = 10_000

export function isCurrentDeleteSuccessNotice(notice, autoDismissId) {
  return notice != null && notice.autoDismissId === autoDismissId
}

export function createPaperDeleteSuccessNoticeController(options = {}) {
  const durationMs = options.durationMs ?? PAPER_DELETE_SUCCESS_NOTICE_MS
  const setTimeoutFn = options.setTimeoutFn ?? ((fn, ms) => setTimeout(fn, ms))
  const clearTimeoutFn = options.clearTimeoutFn ?? ((id) => clearTimeout(id))

  let nextId = 1
  let currentId = null
  let timer

  const stopTimer = () => {
    if (timer !== undefined) {
      clearTimeoutFn(timer)
      timer = undefined
    }
  }

  return {
    show(notice, setNotice, clearIfCurrent) {
      const autoDismissId = nextId++
      currentId = autoDismissId
      stopTimer()
      setNotice({
        tone: 'success',
        title: notice.title,
        detail: notice.detail,
        autoDismissId,
      })
      timer = setTimeoutFn(() => {
        timer = undefined
        if (currentId === autoDismissId) {
          clearIfCurrent(autoDismissId)
        }
      }, durationMs)
      return autoDismissId
    },

    dispose() {
      currentId = null
      stopTimer()
    },
  }
}
