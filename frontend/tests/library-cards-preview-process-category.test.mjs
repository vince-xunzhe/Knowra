import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { describe, it } from 'node:test'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = relative => readFileSync(join(root, relative), 'utf8')

describe('library cards preview, process action and category', () => {
  it('shows a process action only for unfinished papers', () => {
    const meta = read('src/components/paperProcessMeta.ts')
    assert.match(meta, /export function paperCardProcessAction/)
    assert.match(meta, /if \(paper\.processed\) return null/)
    assert.match(meta, /kind: 'retry', label: tr\("重试处理"\)/)
    assert.match(meta, /kind: 'process', label: tr\("立即处理"\)/)
    const page = read('src/pages/PapersPage.tsx')
    assert.match(page, /paperCardProcessAction\(/)
    assert.match(page, /action\?\.kind === 'retry'/)
    assert.match(page, /data-testid="paper-card-process"/)
  })

  it('wires unprocessed covers, isolated process buttons, and confirmation', () => {
    const source = read('src/pages/PapersPage.tsx')
    assert.match(source, /function PaperCover\(/)
    assert.match(source, /firstPageUrl\(paper\.id\)/)
    assert.doesNotMatch(source, /\{paper\.processed \? \([\s\S]*firstPageUrl/)
    assert.match(source, /data-testid="paper-card-process"/)
    assert.match(source, /event\.stopPropagation\(\)/)
    assert.match(source, /confirmIfNeeded\(/)
    assert.match(source, /if \(confirmation === false\) return/)
    assert.match(source, /disabled=\{pending\}/)
  })

  it('renders long category names in full width without card truncate classes', () => {
    const source = read('src/pages/PapersPage.tsx')
    const start = source.indexOf('function inheritedCategoryOptionLabel')
    const editor = source.slice(start >= 0 ? start : source.indexOf('function InlinePaperCategoryEditor'))
    assert.match(editor, /data-testid="paper-card-category-label"/)
    assert.match(editor, /break-words/)
    assert.match(editor, /whitespace-normal/)
    assert.match(editor, /w-full min-w-0 max-w-full/)
    assert.match(editor, /跟随 ·/)
    assert.doesNotMatch(editor, /max-w-32/)
    assert.doesNotMatch(editor, /max-w-36/)
  })
})
