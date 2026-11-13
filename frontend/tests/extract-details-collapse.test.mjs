import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { describe, it } from 'node:test'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const reviewPage = readFileSync(join(root, 'src/pages/ReviewPage.tsx'), 'utf8')
const hints = readFileSync(join(root, 'src/pages/extractDetailsHints.ts'), 'utf8')
const en = JSON.parse(readFileSync(join(root, 'src/i18n/locales/en.json'), 'utf8'))
const ja = JSON.parse(readFileSync(join(root, 'src/i18n/locales/ja.json'), 'utf8'))
const es = JSON.parse(readFileSync(join(root, 'src/i18n/locales/es.json'), 'utf8'))

const KEYS = [
  '抽取详情',
  '原文覆盖不完整',
  '失败页 {0}',
  '存在失败页',
  '使用了有长度上限的本地文本兜底',
  '处理失败：{0}',
]

describe('review extract details collapse', () => {
  it('folds the three extract-meta cards behind a default-closed 抽取详情 control', () => {
    assert.match(reviewPage, /function ExtractDetailsSection\(/)
    assert.match(reviewPage, /useState\(false\)/)
    assert.match(reviewPage, /key=\{visibleDetail\.id\}/)
    assert.match(reviewPage, /tr\("抽取详情"\)/)
    assert.match(reviewPage, /aria-expanded=\{open\}/)
    assert.match(reviewPage, /aria-controls=\{panelId\}/)
    assert.match(reviewPage, /role="region"/)
    assert.match(reviewPage, /data-testid="review-extract-details-toggle"/)
    assert.match(reviewPage, /collectExtractDetailHints\(/)

    const disclosureIndex = reviewPage.indexOf('function ExtractDetailsSection')
    const structuredIndex = reviewPage.indexOf('function StructuredBody')
    const processIndex = reviewPage.indexOf('tr("处理阶段：")')
    const coverageIndex = reviewPage.indexOf('tr("原文覆盖")')
    const compareIndex = reviewPage.indexOf('tr("原文对照")')
    const evidenceButton = reviewPage.indexOf('tr("逐页核对原文")')
    const contributionIndex = reviewPage.indexOf('tr("核心贡献")')
    const abstractIndex = reviewPage.indexOf('title={tr("摘要")}')

    assert.ok(disclosureIndex >= 0)
    assert.ok(processIndex > disclosureIndex)
    assert.ok(coverageIndex > processIndex)
    assert.ok(compareIndex > coverageIndex)
    assert.ok(reviewPage.indexOf('</ExtractDetailsSection>') > compareIndex)
    assert.ok(evidenceButton >= 0)
    assert.ok(contributionIndex > structuredIndex)
    assert.ok(abstractIndex > structuredIndex)
    assert.ok(contributionIndex > reviewPage.indexOf('</ExtractDetailsSection>'))
    assert.ok(abstractIndex > reviewPage.indexOf('</ExtractDetailsSection>'))
  })

  it('keeps concise exception hints available while details are collapsed', () => {
    assert.match(hints, /export function collectExtractDetailHints/)
    assert.match(hints, /processStage === 'failed'/)
    assert.match(hints, /原文覆盖不完整/)
    assert.match(hints, /失败页 \{0\}/)
    assert.match(hints, /使用了有长度上限的本地文本兜底/)
    assert.match(reviewPage, /data-testid="review-extract-details-hint"/)
  })

  it('translates the new disclosure and hint labels', () => {
    for (const key of KEYS) {
      assert.ok(en[key], `missing en: ${key}`)
      assert.ok(ja[key], `missing ja: ${key}`)
      assert.ok(es[key], `missing es: ${key}`)
      assert.notEqual(en[key], key)
    }
  })
})
