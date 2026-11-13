import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  confirmationForRoute,
  decidePaperExtractRoute,
} from '../src/api/paperExtractConfirm.ts'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')

function route(overrides = {}) {
  return {
    provider_id: 'openai',
    provider_type: 'openai',
    provider_label: 'OpenAI 原生',
    model_id: 'openai/gpt-4o',
    model_label: 'GPT-4o',
    upstream_model: 'gpt-4o',
    runtime_model_name: 'gpt-4o',
    surface: 'openai_file_search',
    file_search_api: 'assistants',
    requires_file_search_confirmation: true,
    supported: true,
    unsupported_reason: null,
    route_fingerprint: 'fp-openai-gpt-4o',
    has_custom_base_url: false,
    pricing_url: 'https://platform.openai.com/docs/pricing',
    file_search_guide_url: 'https://platform.openai.com/docs/guides/tools-file-search',
    ...overrides,
  }
}

test('Codex CLI extract route does not require the paid file_search confirmation', () => {
  assert.deepEqual(
    decidePaperExtractRoute(route({
      provider_id: 'codex-cli',
      provider_type: 'codex_cli',
      surface: 'codex_cli_hierarchy',
      file_search_api: null,
      requires_file_search_confirmation: false,
      model_id: 'codex-cli/gpt-6-astra',
      route_fingerprint: 'fp-codex',
    })),
    { status: 'not_required' },
  )
})

test('OpenAI file_search asks the user to confirm against the live fingerprint', () => {
  const current = route()
  assert.deepEqual(decidePaperExtractRoute(current), {
    status: 'needs_confirmation',
    route: current,
  })
  assert.deepEqual(confirmationForRoute(current), {
    acknowledged: true,
    route_fingerprint: 'fp-openai-gpt-4o',
  })
})

test('compatible or unresolved extract routes are blocked with the server reason', () => {
  const blocked = decidePaperExtractRoute(route({
    provider_id: 'kimi',
    provider_type: 'openai_compatible',
    supported: false,
    requires_file_search_confirmation: false,
    unsupported_reason: '当前论文抽取绑定的是 OpenAI 兼容 API。',
    route_fingerprint: 'fp-kimi',
  }))
  assert.equal(blocked.status, 'blocked')
  assert.match(blocked.message, /兼容 API/)
})

test('every extract start entry confirms before creating a job', () => {
  const entries = [
    'src/pages/PapersPage.tsx',
    'src/pages/ReviewPage.tsx',
    'src/hooks/usePipelineState.ts',
    'src/components/PipelineConsole.tsx',
    'src/components/BackgroundTasks.tsx',
  ]
  for (const rel of entries) {
    const source = readFileSync(resolve(root, rel), 'utf8')
    assert.match(source, /confirmIfNeeded\(/, `${rel} must request confirmation`)
    if (rel.endsWith('BackgroundTasks.tsx')) {
      assert.match(source, /resumeJob\([\s\S]*?confirmation/, `${rel} must pass confirmation into resume`)
    }
  }
  const pipeline = readFileSync(resolve(root, 'src/components/PipelineConsole.tsx'), 'utf8')
  assert.match(pipeline, /startPipeline\([\s\S]*?confirmation/, 'full pipeline must pass confirmation')
})
