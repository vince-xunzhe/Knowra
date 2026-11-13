import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { describe, it } from 'node:test'
import { fileURLToPath } from 'node:url'
import { pathToFileURL } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

class FakeNode {
  constructor() {
    this.parent = null
    this.children = new Set()
  }
  append(child) {
    child.parent = this
    this.children.add(child)
  }
  contains(node) {
    let current = node
    while (current) {
      if (current === this) return true
      current = current.parent
    }
    return false
  }
}

class FakeEventTarget {
  constructor() {
    this.listeners = new Map()
  }
  key(type, capture) {
    return `${type}:${Boolean(capture)}`
  }
  addEventListener(type, fn, capture) {
    const key = this.key(type, capture)
    this.listeners.set(key, (this.listeners.get(key) || []).concat(fn))
  }
  removeEventListener(type, fn, capture) {
    const key = this.key(type, capture)
    this.listeners.set(key, (this.listeners.get(key) || []).filter(item => item !== fn))
  }
  emit(type, event, capture = true) {
    for (const fn of this.listeners.get(this.key(type, capture)) || []) fn(event)
  }
  count(type, capture = true) {
    return (this.listeners.get(this.key(type, capture)) || []).length
  }
  total() {
    return [...this.listeners.values()].reduce((sum, list) => sum + list.length, 0)
  }
}

async function importHook() {
  return import(pathToFileURL(join(root, 'src/hooks/useDismissOnOutside.ts')).href)
}

describe('background tasks outside dismiss', () => {
  it('wires the panel to the shared outside-dismiss helper', () => {
    const source = readFileSync(join(root, 'src/components/BackgroundTasks.tsx'), 'utf8')
    assert.match(source, /useDismissOnOutside\(/)
    assert.match(source, /rootRef/)
    assert.match(source, /aria-expanded=\{open\}/)
    assert.match(source, /aria-controls="background-tasks-panel"/)
    assert.match(source, /setOpen\(value => !value\)/)
  })

  it('dismisses outside pointer and Escape, keeps inside events, and unbinds listeners', async () => {
    const { bindOutsideDismiss } = await importHook()
    const rootNode = new FakeNode()
    const inside = new FakeNode()
    const outside = new FakeNode()
    rootNode.append(inside)
    const target = new FakeEventTarget()
    let dismissed = 0
    const unbind = bindOutsideDismiss({
      root: rootNode,
      onDismiss: () => { dismissed += 1 },
      eventTarget: target,
    })
    assert.equal(target.count('pointerdown', true), 1)
    assert.equal(target.count('keydown', false), 1)

    target.emit('pointerdown', { target: inside }, true)
    assert.equal(dismissed, 0)
    target.emit('pointerdown', { target: rootNode }, true)
    assert.equal(dismissed, 0)
    target.emit('pointerdown', { target: outside, pointerType: 'touch' }, true)
    assert.equal(dismissed, 1)
    target.emit('keydown', { key: 'Enter' }, false)
    assert.equal(dismissed, 1)
    target.emit('keydown', { key: 'Escape' }, false)
    assert.equal(dismissed, 2)

    unbind()
    assert.equal(target.total(), 0)
    target.emit('pointerdown', { target: outside }, true)
    target.emit('keydown', { key: 'Escape' }, false)
    assert.equal(dismissed, 2)
  })

  it('does not accumulate listeners across repeated bind/unbind cycles', async () => {
    const { bindOutsideDismiss } = await importHook()
    const rootNode = new FakeNode()
    const target = new FakeEventTarget()
    let dismissed = 0
    for (let index = 0; index < 8; index++) {
      const unbind = bindOutsideDismiss({
        root: rootNode,
        onDismiss: () => { dismissed += 1 },
        eventTarget: target,
      })
      assert.equal(target.count('pointerdown', true), 1)
      assert.equal(target.count('keydown', false), 1)
      unbind()
      assert.equal(target.total(), 0)
    }
    const unbind = bindOutsideDismiss({
      root: rootNode,
      onDismiss: () => { dismissed += 1 },
      eventTarget: target,
    })
    target.emit('pointerdown', { target: new FakeNode() }, true)
    assert.equal(dismissed, 1)
    unbind()
    assert.equal(target.total(), 0)
  })
})
