import assert from 'node:assert/strict'
import test from 'node:test'
import { computeAnchoredMenuPosition } from '../src/lib/anchoredMenu.ts'

test('opens below the trigger when there is enough room', () => {
  const pos = computeAnchoredMenuPosition(
    { top: 80, left: 40, width: 180, height: 24 },
    { width: 800, height: 600 },
    { menuHeight: 160, preferredMaxHeight: 240 },
  )
  assert.equal(pos.placement, 'bottom')
  assert.equal(pos.top, 80 + 24 + 4)
  assert.equal(pos.left, 40)
  assert.equal(pos.width, 180)
  assert.ok(pos.maxHeight >= 160)
})

test('flips above the trigger near the bottom of the viewport', () => {
  const pos = computeAnchoredMenuPosition(
    { top: 560, left: 20, width: 180, height: 28 },
    { width: 800, height: 600 },
    { menuHeight: 200, preferredMaxHeight: 240 },
  )
  assert.equal(pos.placement, 'top')
  assert.ok(pos.top + pos.maxHeight <= 560 - 4 + 0.01)
  assert.ok(pos.top >= 8)
  assert.ok(pos.maxHeight >= 96)
})

test('clamps to a 390px viewport and stays fully inside it', () => {
  const pos = computeAnchoredMenuPosition(
    { top: 200, left: 300, width: 160, height: 24 },
    { width: 390, height: 800 },
    { menuHeight: 200, minWidth: 160, maxWidth: 374 },
  )
  assert.ok(pos.left >= 8)
  assert.ok(pos.left + pos.width <= 390 - 8 + 0.01)
  assert.ok(pos.top >= 0)
  assert.ok(pos.top + Math.min(200, pos.maxHeight) <= 800)
})
