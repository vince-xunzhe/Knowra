import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { describe, it } from 'node:test'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = relative => readFileSync(join(root, relative), 'utf8')

describe('library paper deletion', () => {
  it('exposes a delete API that evicts the cached list entry', () => {
    const source = read('src/api/client.ts')
    assert.match(source, /export const deletePaper/)
    assert.match(source, /api\.delete<PaperDeleteResult>\(`\/papers\/\$\{id\}`\)/)
    assert.match(source, /cachedPapers = cachedPapers\.filter/)
  })

  it('adds an operations menu and a destructive confirmation on grid, list and the detail strip', () => {
    const source = read('src/pages/PapersPage.tsx')
    assert.match(source, /data-testid="paper-card-actions"/)
    assert.match(source, /data-testid="paper-card-delete"/)
    assert.match(source, /data-testid="paper-delete-dialog"/)
    assert.match(source, /data-testid="paper-delete-confirm"/)
    assert.match(source, /data-testid="paper-detail-delete"/)
    assert.match(source, /onDelete=\{\(\) => requestDeletePaper/)
    assert.match(source, /将永久删除该论文记录/)
    assert.match(source, /此操作不可恢复/)
    assert.match(source, /if \(!deleteTarget \|\| deleteBusy\) return/)
    assert.match(source, /deletePaper\(target\.id\)/)
    assert.match(source, /prev\.filter\(item => item\.id !== target\.id\)/)
    assert.match(source, /prev\?\.id === target\.id \? null : prev/)
  })
})
