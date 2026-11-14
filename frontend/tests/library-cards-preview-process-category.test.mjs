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

  it('clamps titles to a fixed line count and exposes the original name', () => {
    const source = read('src/pages/PapersPage.tsx')
    const title = source.slice(source.indexOf('function PaperTitle'))
    assert.match(title, /data-testid="paper-card-title"/)
    assert.match(title, /title=\{name\}/)
    assert.match(title, /aria-label=\{name\}/)
    assert.match(title, /line-clamp-3/)
    assert.match(title, /line-clamp-2/)
    assert.match(source, /<PaperTitle paper=\{paper\} lines=\{3\} \/>/)
    assert.match(source, /<PaperTitle paper=\{paper\} lines=\{2\} \/>/)
  })

  it('renders one compact category control without a duplicate static label', () => {
    const source = read('src/pages/PapersPage.tsx')
    const start = source.indexOf('function inheritedCategoryOptionLabel')
    const editor = source.slice(start >= 0 ? start : source.indexOf('function InlinePaperCategoryEditor'))
    assert.match(editor, /data-testid="paper-card-category-label"/)
    assert.match(editor, /data-testid="paper-card-category-menu"/)
    assert.match(editor, /跟随 ·/)
    assert.match(editor, /text-\[11px\]/)
    assert.match(editor, /aria-haspopup="listbox"/)
    assert.doesNotMatch(editor, /<select/)
    assert.doesNotMatch(editor, /max-w-32/)
    assert.doesNotMatch(editor, /max-w-36/)
    // The visible control shows the category once; the inherit prefix is not
    // repeated as a static paragraph above a second native select.
    const staticCopy = editor.includes('paper-card-category-label') && editor.includes('<p') && editor.includes('<select')
    assert.equal(staticCopy, false)
    assert.match(editor, /paper-card-category-control/)
    assert.match(editor, /createPortal/)
    assert.match(editor, /useAnchoredMenu/)
    const css = read('src/index.css')
    assert.match(css, /\.paper-card-category-control[\s\S]*font-size:\s*11px/)
  })
})
