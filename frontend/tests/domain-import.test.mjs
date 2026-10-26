import assert from 'node:assert/strict'
import test from 'node:test'
import { availableImportPapers, importedPaperIds } from '../src/lib/domainImport.ts'

const paper = (id, eligible = true) => ({ id, title: '同名论文', eligible })
const node = (id, paperId) => ({ id, kind: 'paper', paperId })
const board = nodes => ({ id: 'synthetic-board', nodes })

test('local Paper identity distinguishes equal titles without requiring source metadata', () => {
  const current = board([node('reference-a', 'local-a')])
  assert.deepEqual(availableImportPapers([paper('local-a'), paper('local-b')], current).map(p => p.id), ['local-b'])
  // Local primary keys are opaque: version-looking suffixes are not arXiv IDs.
  assert.deepEqual(availableImportPapers([paper('local-a-v2')], current).map(p => p.id), ['local-a-v2'])
})

test('selection excludes ineligible, imported and repeated picker rows while preserving legacy references', () => {
  const current = board([node('reference-a', 'a'), node('legacy-a', 'a')])
  const before = structuredClone(current)
  assert.deepEqual(availableImportPapers([paper('a'), paper('pending', false), paper('b'), paper('b')], current).map(p => p.id), ['b'])
  assert.deepEqual(current, before)
  assert.deepEqual([...importedPaperIds(current)], ['a'])
})

test('the next synchronous import uses the changed board and cannot repeat a paper', () => {
  const papers = [paper('a'), paper('b')]
  const current = board([node('reference-a', 'a')])
  const incoming = availableImportPapers(papers, current)
  const after = { ...current, nodes: [...current.nodes, ...incoming.map(p => node(`reference-${p.id}`, p.id))] }
  assert.deepEqual(availableImportPapers(papers, after), [])
  // Removing/undoing a reference makes that local paper selectable again.
  assert.deepEqual(availableImportPapers(papers, current).map(p => p.id), ['b'])
})

test('import state is local to the active board and survives review eligibility changes', () => {
  const imported = board([node('reference-a', 'a')])
  assert.deepEqual(availableImportPapers([paper('a')], board([])).map(p => p.id), ['a'])
  assert.equal(importedPaperIds(imported).has('a'), true)
  assert.deepEqual(availableImportPapers([paper('a', false)], imported), [])
})
