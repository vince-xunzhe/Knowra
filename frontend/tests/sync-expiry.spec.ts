import { expect, test } from '@playwright/test'

const cloudSession = {
  access_token: 'test-token',
  refresh_token: 'test-refresh',
  expires_at: Date.now() + 60 * 60 * 1000,
  user: { id: 'user-1', email: 'user@example.test', display_name: null },
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(session => {
    localStorage.setItem('knowra.cloud.baseUrl', 'https://cloud.test')
    localStorage.setItem('knowra.cloud.session', JSON.stringify(session))
  }, cloudSession)

  // Establish the Vite origin without mounting the application shell; the
  // test imports and exercises the sync service through its module graph.
  await page.goto('/favicon.svg')
})

test('expired commit re-prepares and reuses the uploaded file', async ({ page }) => {
  let prepares = 0
  let commits = 0
  let uploads = 0
  const commitBodies: unknown[] = []

  await page.route('https://cloud.test/api/cloud/me', route =>
    route.fulfill({ status: 401, contentType: 'application/json', body: '{}' }),
  )
  await page.route('https://cloud.test/api/sync/prepare', async route => {
    prepares += 1
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        sync_session_id: `session-${prepares}`,
        expires_at: '2099-01-01T00:00:00Z',
        uploads_required: [{
          rel_path: 'papers/foo.md',
          upload_url: `https://storage.test/upload-${prepares}`,
          method: 'PUT',
          headers: {},
        }],
        uploads_skipped: [],
        validation_errors: [],
      }),
    })
  })
  await page.route('https://cloud.test/api/sync/commit', async route => {
    commits += 1
    commitBodies.push(route.request().postDataJSON())
    if (commits === 1) {
      await route.fulfill({
        status: 410,
        contentType: 'application/json',
        body: JSON.stringify({
          detail: { error: 'expired', message: 'sync_session has expired; rerun prepare' },
        }),
      })
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        revision: 7,
        accepted: { papers: 0, knowledge_nodes: 0, knowledge_edges: 0, wiki_files: 1 },
        rejected: [],
        server_now: '2099-01-01T00:00:01Z',
      }),
    })
  })
  await page.route('https://storage.test/**', async route => {
    uploads += 1
    await route.fulfill({ status: 200, body: '' })
  })

  const result = await page.evaluate(async () => {
    const modulePath = '/src/services/syncAgent.ts'
    const { runSync } = await import(/* @vite-ignore */ modulePath)
    return runSync({
      papers: [],
      knowledge_nodes: [],
      knowledge_edges: [],
      wiki_files: [{
        row: {
          id: 'wiki-1',
          user_id: '',
          kind: 'paper',
          rel_path: 'papers/foo.md',
          content_hash: 'hash-1',
          size_bytes: 5,
        },
        body: 'hello',
      }],
      deletions: {},
      since: null,
    })
  })

  expect(result.revision).toBe(7)
  expect(prepares).toBe(2)
  expect(commits).toBe(2)
  expect(uploads).toBe(1)
  expect(commitBodies).toEqual([
    expect.objectContaining({
      sync_session_id: 'session-1',
      uploaded: [{ rel_path: 'papers/foo.md', content_hash: 'hash-1' }],
    }),
    expect.objectContaining({
      sync_session_id: 'session-2',
      uploaded: [{ rel_path: 'papers/foo.md', content_hash: 'hash-1' }],
    }),
  ])
})

test('expired resume clears the stale pending session', async ({ page }) => {
  await page.route('https://cloud.test/api/sync/commit', route =>
    route.fulfill({
      status: 410,
      contentType: 'application/json',
      body: JSON.stringify({
        detail: { error: 'expired', message: 'sync_session has expired; rerun prepare' },
      }),
    }),
  )

  const result = await page.evaluate(async () => {
    localStorage.setItem('knowra.sync.pendingSession', JSON.stringify({
      sync_session_id: 'expired-session',
      expires_at: '2099-01-01T00:00:00Z',
      uploaded: [{ rel_path: 'papers/foo.md', content_hash: 'hash-1' }],
    }))
    const modulePath = '/src/services/syncAgent.ts'
    const { resumeCommit } = await import(/* @vite-ignore */ modulePath)
    const resumed = await resumeCommit()
    return {
      resumed,
      pending: localStorage.getItem('knowra.sync.pendingSession'),
    }
  })

  expect(result).toEqual({ resumed: null, pending: null })
})
