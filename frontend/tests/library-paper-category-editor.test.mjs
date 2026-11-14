import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { describe, it } from 'node:test'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = relative => readFileSync(join(root, relative), 'utf8')

describe('knowledge paper category editor', () => {
  it('places 大类编辑 on the knowledge filter bar, not beside 新增概念', () => {
    const page = read('src/pages/GraphPage.tsx')
    const button = read('src/components/KnowledgePaperCategoryEditorButton.tsx')
    assert.match(button, /data-testid="paper-category-editor"/)
    assert.match(button, /大类编辑/)
    assert.match(page, /data-testid="knowledge-title-bar"/)
    assert.match(page, /data-testid="knowledge-filter-bar"/)
    const filterIdx = page.indexOf('data-testid="knowledge-filter-bar"')
    const buttonIdx = page.indexOf('<KnowledgePaperCategoryEditorButton')
    const titleIdx = page.indexOf('data-testid="knowledge-title-bar"')
    const newConceptIdx = page.indexOf('tr("新增概念")')
    assert.ok(filterIdx > 0 && buttonIdx > filterIdx)
    assert.ok(titleIdx > 0 && newConceptIdx > titleIdx && newConceptIdx < filterIdx)
    assert.ok(page.indexOf('tr("编排大类")') === -1)
  })

  it('sends a transfer target when deleting an occupied paper category', () => {
    const client = read('src/api/client.ts')
    assert.match(client, /export const deletePaperCategory = \(name: string, target\?: string \| null\)/)
    assert.match(client, /params: target \? \{ target \} : \{\}/)
  })

  it('confirms rename with old and new names and does not treat category delete as paper delete', () => {
    const composer = read('src/components/CategoryComposer.tsx')
    assert.match(composer, /data-testid="paper-category-editor-panel"/)
    assert.match(composer, /data-testid="paper-category-rename-dialog"/)
    assert.match(composer, /data-testid="paper-category-delete-dialog"/)
    assert.match(composer, /将「\{0\}」重命名为「\{1\}」/)
    assert.match(composer, /必须先选择另一个已有大类/)
    assert.match(composer, /不会删除任何论文/)
    assert.match(composer, /data-testid="paper-category-delete-target"/)
    assert.match(composer, /请先新增大类/)
    assert.doesNotMatch(composer, /该类论文回退为跟随模型/)
    assert.match(composer, /role="dialog"/)
    assert.match(composer, /aria-modal="true"/)
    assert.match(composer, /PAPER_CATEGORIES_CHANGED_EVENT|notifyPaperCategoriesChanged|deletePaperCategory\(\s*deleteConfirm\.name/)
  })

  it('reloads library and review category views when the taxonomy changes', () => {
    const library = read('src/pages/PapersPage.tsx')
    const review = read('src/pages/ReviewPage.tsx')
    assert.match(library, /PAPER_CATEGORIES_CHANGED_EVENT/)
    assert.match(review, /PAPER_CATEGORIES_CHANGED_EVENT/)
  })
})
