import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { describe, it } from 'node:test'
import { fileURLToPath } from 'node:url'
import {
  PAPER_DELETE_SUCCESS_NOTICE_MS,
  createPaperDeleteSuccessNoticeController,
  isCurrentDeleteSuccessNotice,
} from '../src/pages/paperDeleteSuccessNotice.mjs'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = relative => readFileSync(join(root, relative), 'utf8')

function createClock() {
  let now = 0
  let nextHandle = 1
  const timers = new Map()

  const fireDue = () => {
    const due = [...timers.entries()]
      .filter(([, timer]) => timer.due <= now)
      .sort((a, b) => a[1].due - b[1].due || a[0] - b[0])
    for (const [id, timer] of due) {
      if (!timers.has(id)) continue
      timers.delete(id)
      timer.fn()
    }
  }

  return {
    now: () => now,
    pending: () => timers.size,
    setTimeoutFn(fn, ms) {
      const id = nextHandle++
      timers.set(id, { fn, due: now + ms })
      return id
    },
    clearTimeoutFn(id) {
      timers.delete(id)
    },
    advance(ms) {
      now += ms
      fireDue()
    },
  }
}

function bindController(clock) {
  let notice = null
  const controller = createPaperDeleteSuccessNoticeController({
    setTimeoutFn: clock.setTimeoutFn,
    clearTimeoutFn: clock.clearTimeoutFn,
  })
  const show = (title, detail = 'deleted detail') => controller.show(
    { title, detail },
    next => { notice = next },
    id => { if (isCurrentDeleteSuccessNotice(notice, id)) notice = null },
  )
  return {
    get notice() { return notice },
    setNotice(next) { notice = next },
    show,
    dispose: () => controller.dispose(),
  }
}

describe('library delete success notice timer', () => {
  it('keeps the official auto-dismiss window at 10000ms', () => {
    assert.equal(PAPER_DELETE_SUCCESS_NOTICE_MS, 10000)
  })

  it('shows the success notice immediately and keeps it visible before the timeout', () => {
    const clock = createClock()
    const ui = bindController(clock)
    ui.show('已永久删除「A」')
    assert.equal(ui.notice?.tone, 'success')
    assert.equal(ui.notice?.title, '已永久删除「A」')
    clock.advance(PAPER_DELETE_SUCCESS_NOTICE_MS - 1)
    assert.equal(ui.notice?.title, '已永久删除「A」')
  })

  it('clears the success notice once the timeout elapses', () => {
    const clock = createClock()
    const ui = bindController(clock)
    ui.show('已永久删除「A」')
    clock.advance(PAPER_DELETE_SUCCESS_NOTICE_MS)
    assert.equal(ui.notice, null)
  })

  it('restarts the timer for a later success message and does not let the old timer clear it early', () => {
    const clock = createClock()
    const ui = bindController(clock)
    ui.show('已永久删除「A」')
    clock.advance(4_000)
    ui.show('已永久删除「B」')
    assert.equal(ui.notice?.title, '已永久删除「B」')
    clock.advance(PAPER_DELETE_SUCCESS_NOTICE_MS - 1)
    assert.equal(ui.notice?.title, '已永久删除「B」')
    clock.advance(1)
    assert.equal(ui.notice, null)
  })

  it('does not let a disposed (unmounted) timer clear a notice', () => {
    const clock = createClock()
    const ui = bindController(clock)
    ui.show('已永久删除「A」')
    ui.dispose()
    assert.equal(ui.notice?.title, '已永久删除「A」')
    clock.advance(PAPER_DELETE_SUCCESS_NOTICE_MS)
    assert.equal(ui.notice?.title, '已永久删除「A」')
    assert.equal(clock.pending(), 0)
  })

  it('does not auto-hide a later failure warning when an older success timer fires', () => {
    const clock = createClock()
    const ui = bindController(clock)
    ui.show('已永久删除「A」')
    clock.advance(1_000)
    ui.setNotice({ tone: 'warning', title: '删除失败，请重试' })
    clock.advance(PAPER_DELETE_SUCCESS_NOTICE_MS)
    assert.equal(ui.notice?.tone, 'warning')
    assert.equal(ui.notice?.title, '删除失败，请重试')
  })

  it('wires only delete-success notices to the 10s controller and cleans up on unmount', () => {
    const page = read('src/pages/PapersPage.tsx')
    assert.match(page, /createPaperDeleteSuccessNoticeController/)
    assert.match(page, /isCurrentDeleteSuccessNotice/)
    assert.match(page, /PAPER_DELETE_SUCCESS_NOTICE_MS|deleteSuccessNotice\.current\.show/)
    assert.match(page, /deleteSuccessNotice\.current\.dispose\(\)/)
    assert.match(page, /deleteSuccessNotice\.current\.show\(/)
    assert.match(page, /data-testid="paper-delete-success-notice"|testId=\{actionNotice\.autoDismissId/)
    assert.doesNotMatch(page, /tone: 'error'[\s\S]{0,200}deleteSuccessNotice\.current\.show/)
    assert.doesNotMatch(page, /tone: 'warning'[\s\S]{0,200}deleteSuccessNotice\.current\.show/)
  })
})
