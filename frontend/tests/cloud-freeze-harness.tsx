// Production components and clients, with synthetic API responses supplied by
// the browser runner. No app backend, filesystem, worker or model is started.
import { StrictMode, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { CloudAuthProvider, useCloudAuth } from '../src/hooks/useCloudAuth'
import CloudSyncSection from '../src/components/CloudSyncSection'
import SyncStageCard from '../src/components/SyncStageCard'
import PipelineConsole from '../src/components/PipelineConsole'
import RecommendPage from '../src/pages/RecommendPage'
import * as cloud from '../src/api/cloud'
import * as sync from '../src/services/syncAgent'
import { gatherLocalSnapshot, previewSnapshotCounts } from '../src/services/gatherLocalSnapshot'
import { getConfig, updateConfig, testModelGatewayProvider, startLocalRecommendationWorker } from '../src/api/client'
import '../src/index.css'

interface ProbeResult { name: string; status: string; code?: string; message?: string }
const operations: Record<string, () => unknown> = {
  signIn: () => cloud.cloudSignIn('fixture@example.invalid', 'synthetic-password'),
  signUp: () => cloud.cloudSignUp('fixture@example.invalid', 'synthetic-password'),
  refreshSession: () => cloud.cloudRefreshSession('synthetic-refresh-token'),
  signOut: cloud.cloudSignOut,
  accessToken: cloud.getFreshAccessToken,
  setConfig: () => cloud.setCloudConfig({ baseUrl: 'https://modified.example.invalid' }),
  setLastSyncAt: () => cloud.setLastSyncAt('2030-01-01T00:00:00Z'),
  getDeviceId: cloud.getDeviceId,
  prepare: () => cloud.cloudPrepare({ device_id: 'synthetic-device' }),
  commit: () => cloud.cloudCommit({ sync_session_id: 'synthetic-session', uploaded: [] }),
  me: cloud.cloudMe,
  recommendations: cloud.cloudRecommendations,
  tags: cloud.cloudRecTags,
  refreshRecommendations: cloud.cloudRefreshRecommendations,
  pushSummary: () => cloud.cloudPushRecSummary('synthetic-paper', 'Synthetic summary'),
  recMarks: cloud.cloudRecMarks,
  addRecMark: () => cloud.cloudAddRecMark('synthetic-paper'),
  removeRecMark: () => cloud.cloudRemoveRecMark('synthetic-paper'),
  warmup: cloud.cloudWarmup,
  signedUpload: () => cloud.performSignedUpload({ rel_path: 'synthetic.md', upload_url: 'https://storage.example.invalid/signed-upload', method: 'PUT', headers: {} }, 'Synthetic wiki'),
  personalRecommendations: cloud.personalRecommendations,
  focus: () => cloud.saveRecommendationFocus('Synthetic focus'),
  refreshPersonal: cloud.refreshPersonalRecommendations,
  feedback: () => cloud.recommendationEvent('synthetic-batch', 'synthetic-paper', 'viewed'),
  registerWorker: () => cloud.registerRecommendationWorker('synthetic-worker'),
  revokeWorker: () => cloud.revokeRecommendationWorker('synthetic-worker'),
  startRemoteWorker: () => startLocalRecommendationWorker('https://cloud.example.invalid', 'synthetic-worker-token'),
  runSync: () => sync.runSync(sync.emptySnapshot()),
  resume: sync.resumeCommit,
  gather: gatherLocalSnapshot,
  preview: previewSnapshotCounts,
}

async function directProbes(): Promise<ProbeResult[]> {
  const results: ProbeResult[] = []
  for (const [name, operation] of Object.entries(operations)) {
    let timer: ReturnType<typeof setTimeout> | undefined
    try {
      await Promise.race([
        Promise.resolve().then(operation),
        new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('Guard did not reject immediately')), 400) }),
      ])
      results.push({ name, status: 'unexpected-success' })
    } catch (error) {
      const value = error as { code?: string; message?: string }
      results.push({ name, status: 'rejected', code: value.code, message: value.message })
    } finally { clearTimeout(timer) }
  }
  return results
}

function dormantState() {
  return { session: cloud.getStoredSession(), pending: sync.getPendingResume(), configured: cloud.isCloudConfigured(), config: cloud.getCloudConfig() }
}

const fixture = {
  directProbes, dormantState,
  async localModelClients() {
    const initial = await getConfig()
    const saved = await updateConfig({ vlm_model: 'synthetic-model' })
    const checked = await testModelGatewayProvider('synthetic-provider')
    return { initial: initial.vlm_model, saved: saved.vlm_model, result: checked.result.status }
  },
  scanCalls: 0, refreshCalls: 0,
}
declare global { interface Window { cloudFreezeFixture: typeof fixture } }
window.cloudFreezeFixture = fixture

const noAction = async () => {}
const pipeline: Parameters<typeof PipelineConsole>[0]['state'] = {
  freshness: null, promotion: null, promotionRunStatus: null, processing: null,
  compileStatus: null, lintStatus: null, promotionPromptConfigured: true,
  unprocessedHint: 0, loading: false, mutationToken: 0,
  stages: [
    { id: 'ingest', index: '①', label: '录入', tone: 'ok', headline: '合成论文已就绪', isNext: false },
    { id: 'curate', index: '②', label: '筛选', tone: 'ok', headline: '筛选已完成', isNext: false },
    { id: 'compile', index: '③', label: '编译', tone: 'ok', headline: 'Wiki 已就绪', isNext: false },
    { id: 'maintain', index: '④', label: '健检', tone: 'ok', headline: '健检已完成', isNext: false },
  ],
  nextStep: { stage: 'maintain', label: '本地研究就绪', reason: '独立合成验收', tone: 'emerald', run: noAction, busy: false, disabled: true },
  refresh: () => { fixture.refreshCalls++ },
  scan: async () => { fixture.scanCalls++; return { new_found: 0, duplicates: 0, duplicate_files: [], total: 1, unprocessed: 0 } },
  process: noAction, retryFailed: noAction, acceptPromotion: noAction,
  recompilePapers: noAction, recompileConcepts: noAction, openLint: () => {},
  runPromotionRun: async () => { throw new Error('This synthetic harness never starts promotion') },
}

export function Harness() {
  const [page, setPage] = useState('cloud')
  const [expanded, setExpanded] = useState(true)
  const auth = useCloudAuth()
  return <main className="min-h-screen bg-[var(--surface-0b0d12)] text-slate-200">
    <nav aria-label="验收页面" className="flex items-center gap-3 border-b border-slate-800 px-8 py-4">
      <strong className="mr-5 text-lg">Knowra</strong>
      {[['cloud', '云同步设置'], ['recommend', '论文推荐'], ['pipeline', '本地全流程']].map(([key, title]) =>
        <button key={key} onClick={() => setPage(key)} className={`rounded-lg px-4 py-2 text-sm ${page === key ? 'bg-indigo-500/20 text-indigo-200' : 'text-slate-400'}`}>{title}</button>)}
      <span className="ml-auto text-xs text-slate-500">合成数据 · 隔离验收</span>
    </nav>
    <output data-testid="auth-state" hidden>{JSON.stringify({ configured: auth.configured, user: auth.user, session: auth.session })}</output>
    {page === 'cloud' && <div data-testid="cloud-panels" className="mx-auto grid max-w-6xl grid-cols-[1fr_380px] gap-8 p-10">
      <section><h1 className="mb-5 text-xl font-semibold">设置 · 云同步</h1><CloudSyncSection /></section>
      <section><h2 className="mb-5 text-xl font-semibold">研究流程 · 同步</h2><SyncStageCard expanded={expanded} onToggle={() => setExpanded(value => !value)} /></section>
    </div>}
    {page === 'recommend' && <section data-testid="recommend-page" className="h-[950px]"><RecommendPage /></section>}
    {page === 'pipeline' && <section data-testid="pipeline-page" className="mx-auto grid max-w-6xl grid-cols-[340px_1fr] gap-8 p-10">
      <PipelineConsole state={pipeline} candidateMode="off" onCandidateModeChange={() => {}} onOpenLint={() => {}} onOpenRescue={() => {}} onOpenAsk={() => {}} />
      <div className="rounded-xl border border-slate-800 p-7"><h1 className="text-xl font-semibold">本地研究流程</h1><p className="mt-4 text-sm text-slate-400">真实流水线组件；任务响应由合成夹具提供。不会启动后端、任务 worker 或模型服务。</p></div>
    </section>}
  </main>
}

createRoot(document.getElementById('root')!).render(<StrictMode><CloudAuthProvider><Harness /></CloudAuthProvider></StrictMode>)
