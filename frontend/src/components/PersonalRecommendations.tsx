import { useCallback, useEffect, useRef, useState } from 'react'
import { personalRecommendations, saveRecommendationFocus, refreshPersonalRecommendations, recommendationEvent,
  registerRecommendationWorker, revokeRecommendationWorker, getCloudConfig, PersonalRecommendationsUnavailableError, type PersonalFeed, type PersonalRecItem } from '../api/cloud'
import { importRecommendation, localRecommendationWorker, startLocalRecommendationWorker, stopLocalRecommendationWorker } from '../api/client'
import { gatherLocalSnapshot } from '../services/gatherLocalSnapshot'
import { runSync } from '../services/syncAgent'

const labels: Record<string, string> = { domain: '领域', problem: '问题', method: '方法', dataset: '数据集', team: '团队' }
const lanes = { long_term: '长期兴趣', recent: '当前课题', explore: '相邻探索' }
const button = 'rounded-lg border border-slate-700 px-3 py-1.5 text-sm hover:bg-slate-800 disabled:opacity-50'
function errorMessage(error: unknown) {
  const value = error as { response?: { data?: { detail?: unknown } }; message?: string }
  const detail = value.response?.data?.detail
  return typeof detail === 'string' ? detail : value.message || '操作失败，请重试'
}

export default function PersonalRecommendations({ onBrowseAll }: { onBrowseAll: () => void }) {
  const [data, setData] = useState<PersonalFeed | null>(null)
  const [focus, setFocus] = useState('')
  const initialized = useRef(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [unavailable, setUnavailable] = useState(false)
  const [localError, setLocalError] = useState('')
  const [nodeId, setNodeId] = useState('research-worker')
  const [token, setToken] = useState('')
  const [localRunning, setLocalRunning] = useState(false)
  const acceptFeed = useCallback((next: PersonalFeed) => {
    setData(next); setUnavailable(false); setError('')
    if (!initialized.current) { setFocus(next.profile.current_focus); initialized.current = true }
  }, [])
  const reportFeedError = useCallback((error: unknown) => {
    setUnavailable(error instanceof PersonalRecommendationsUnavailableError)
    setError(errorMessage(error))
  }, [])
  const load = useCallback(async () => {
    try { acceptFeed(await personalRecommendations()) }
    catch (error) { reportFeedError(error); throw error }
  }, [acceptFeed, reportFeedError])
  const loadLocalWorker = useCallback(async () => {
    try {
      setLocalRunning((await localRecommendationWorker()).running)
      setLocalError('')
    } catch {
      setLocalError('暂时无法连接本机节点管理，请启动或重启新版桌面后端。云端精选仍可查看。')
    }
  }, [])
  useEffect(() => {
    const refresh = () => {
      void personalRecommendations().then(acceptFeed).catch(reportFeedError)
      void loadLocalWorker()
    }
    refresh()
    const timer = window.setInterval(refresh, 60000)
    return () => clearInterval(timer)
  }, [acceptFeed, reportFeedError, loadLocalWorker])

  async function action(name: string, fn: () => Promise<unknown>) {
    setBusy(name); setError(''); setNotice('')
    try { await fn(); await load(); await loadLocalWorker() } catch (e) { setError(errorMessage(e)) } finally { setBusy('') }
  }

  async function adopt(item: { arxiv_id: string; title: string; authors?: string[] }, batchId: string) {
    await recommendationEvent(batchId, item.arxiv_id, 'requested')
    await importRecommendation(item)
    setNotice('已加入本地知识库，正在同步采纳记录…')
    try {
      await runSync(await gatherLocalSnapshot())
      setNotice('已入库并同步，后续精选将参考这次采纳。')
    } catch {
      throw new Error('论文已入库，但云同步尚未完成。请在云同步中重试；采纳反馈会在同步成功后记录。')
    }
  }

  return <div className="h-full overflow-y-auto bg-[#0b0d12] p-6 text-slate-200">
    <div className="mx-auto max-w-5xl space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div><h1 className="text-xl font-semibold">为你精选</h1><p className="mt-1 text-sm text-slate-400">沿着你的长期兴趣，每周一、三、五精选最多 10 篇。加入知识库，让后续推荐更了解你。</p></div>
        <button className={button} disabled={!!busy || !data || unavailable} onClick={() => void action('refresh', async () => {
          const result = await refreshPersonalRecommendations()
          setNotice(result.status === 'empty_library' ? '请先将论文知识库同步到云端。' : '已提交精选任务；执行节点在线后会自动处理。')
        })}>{busy === 'refresh' ? '提交中…' : '更新精选'}</button>
      </header>
      {unavailable && <section role="status" className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-5">
        <h2 className="font-medium text-amber-100">个性化推荐服务尚未就绪</h2>
        <p className="mt-2 text-sm leading-relaxed text-slate-300">当前连接的云端服务尚未提供此功能，需要完成服务升级。你的知识库不受影响，可以先浏览全部论文。</p>
        <p className="mt-2 text-xs text-slate-400">本机执行节点负责 AI 计算，推荐任务和反馈仍由云端服务管理。</p>
        <div className="mt-4 flex gap-3"><button className={button} onClick={onBrowseAll}>浏览全部论文</button><button className={button} disabled={!!busy} onClick={() => void action('retry', load)}>重新检查服务</button></div>
      </section>}
      {error && !unavailable && <div role="alert" className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-200">{error}<button className="ml-3 underline" onClick={() => void action('retry', load)}>重试</button></div>}
      {notice && <p role="status" className="text-sm text-indigo-200">{notice}</p>}
      {!data && !error && <p>正在读取个人推荐…</p>}
      {data && !unavailable && <>
        <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-4 text-sm">
          <div className="flex flex-wrap justify-between gap-2">
            <span className={data.worker_status === 'online' ? 'text-emerald-300' : 'text-amber-200'}>
              {data.worker_status === 'online' ? '执行节点在线' : data.worker_status === 'offline' ? '执行节点离线 · 新精选等待恢复后生成' : '尚未连接执行节点 · 请在下方设置'}</span>
            <span className="text-slate-400">最近精选：{data.batch?.completed_at ? new Date(data.batch.completed_at).toLocaleString() : '尚未生成'}</span>
          </div>
          {data.workers.map(w => <p key={w.node_id} className="mt-1 text-xs text-slate-400">{w.node_id} · {w.health} · 最近在线 {w.last_seen_at ? new Date(w.last_seen_at).toLocaleString() : '尚未连接'}</p>)}
          {data.job && data.job.status !== 'completed' && <p className="mt-2 text-slate-400">当前任务：{{ queued: '等待执行', running: '正在生成', failed: '执行失败' }[data.job.status] || data.job.status} {data.job.error && `· ${data.job.error}`}</p>}
          {data.batch?.error && <p className="mt-2 text-amber-200">{data.batch.error}</p>}
        </section>
        <details className="rounded-xl border border-slate-800 p-4">
          <summary className="cursor-pointer">我的兴趣画像 · {data.profile.paper_count} 篇论文 · 版本 {data.profile.version}</summary>
          <div className="mt-3 space-y-2 text-sm text-slate-400">{Object.entries(data.profile.dimensions).map(([dimension, values]) => <p key={dimension}><span className="text-slate-200">{labels[dimension]}：</span>{Object.keys(values).slice(0, 10).join('、') || '暂无足够信息'}</p>)}</div>
          <label className="mt-4 block text-sm" htmlFor="rec-focus">当前课题（可选，留空时从近期入库自动推断）</label>
          <textarea id="rec-focus" value={focus} maxLength={2000} onChange={e => setFocus(e.target.value)} rows={2} className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-950 p-3" placeholder="例如：关注低成本三维重建中的泛化方法" />
          <button className={button} disabled={!!busy} onClick={() => void action('focus', () => saveRecommendationFocus(focus))}>保存课题</button>
        </details>
        {data.pending_imports.length > 0 && <section className="rounded-xl border border-indigo-500/30 p-4"><h2 className="font-medium">待入库请求</h2><p className="mt-1 text-xs text-slate-400">手机上的入库请求会保留在这里，实际入库成功后才计入采纳。</p>{data.pending_imports.map(item => <div key={item.arxiv_id} className="mt-3 flex items-center gap-3 text-sm"><span className="flex-1">{item.title}</span><button className={button} disabled={!!busy} onClick={() => void action(item.arxiv_id, () => adopt(item, item.batch_id))}>{busy === item.arxiv_id ? '入库中…' : '完成入库'}</button></div>)}</section>}
        {!data.items.length && <div className="rounded-xl border border-dashed border-slate-700 p-10 text-center text-slate-400">{data.profile.paper_count ? '还没有符合条件的精选。连接执行节点后更新；相关性不足时不会凑数。' : '先同步已有论文，让推荐从你的知识库开始。'}</div>}
        <div className="grid gap-4 lg:grid-cols-2">{data.items.map(item => <RecommendationCard key={`${data.batch?.id}:${item.arxiv_id}`} item={item} batchId={data.batch!.id} busy={!!busy}
          onError={e => setError(errorMessage(e))} onAdopt={() => void action(item.arxiv_id, () => adopt(item, data.batch!.id))} />)}</div>
        <p className="text-xs text-slate-500">已浏览 {data.metrics.viewed} 篇 · 已采纳 {data.metrics.adopted} 篇 · 14 天采纳率 {data.metrics.adoption_rate_14d === null ? '等待观察窗口完成' : `${Math.round(data.metrics.adoption_rate_14d * 100)}%`}（成熟样本 {data.metrics.mature_viewed}，目标 30%）。未采纳不会记为不喜欢。</p>
        <details className="rounded-xl border border-slate-800 p-4 text-sm">
          <summary className="cursor-pointer">执行节点与调用预算</summary>
          <p className="my-3 text-slate-400">月度付费调用预算 ¥{data.budget.limit_cny}；已预留上界 ¥{data.budget.reserved_cny.toFixed(2)}。CLI 费用未知时不显示为零费用。节点离线仅在站内提醒。</p>
          <p className="mb-3 text-slate-400">在常驻机器运行推荐 worker。以下凭证只用于这个账号的推荐任务，新生成同名节点凭证会撤销旧凭证。</p>
          {localError && <p role="status" className="mb-3 text-amber-200">{localError}</p>}
          <div className="mb-4 flex gap-2">
            <button className={button} disabled={!!busy || localRunning || !!localError} onClick={() => void action('local-start', async () => {
              const worker = await registerRecommendationWorker('desktop-local')
              await startLocalRecommendationWorker(getCloudConfig().baseUrl, worker.token)
              setNotice('本机节点已启动，使用 Codex CLI。关闭桌面后端或电脑后任务会等待节点恢复。')
            })}>{localRunning ? '本机节点运行中' : '在本机启动节点'}</button>
            <button className={button} disabled={!!busy || !localRunning} onClick={() => void action('local-stop', stopLocalRecommendationWorker)}>停止本机节点</button>
          </div>
          <div className="flex flex-wrap gap-2"><input aria-label="节点名称" className="rounded border border-slate-700 bg-slate-950 px-2" value={nodeId} onChange={e => setNodeId(e.target.value)} /><button disabled={!!busy || !/^[a-zA-Z0-9_-]{1,80}$/.test(nodeId)} className={button} onClick={() => void action('node', async () => setToken((await registerRecommendationWorker(nodeId)).token))}>生成节点凭证</button></div>
          {token && <div className="mt-3 flex gap-2"><input aria-label="新节点凭证" type="password" readOnly value={token} className="min-w-0 flex-1 rounded bg-slate-950 p-2" /><button className={button} onClick={() => void navigator.clipboard.writeText(token).then(() => setNotice('节点凭证已复制')).catch(() => setError('复制失败，请从输入框手动复制'))}>复制</button></div>}
          <p className="mt-2 text-xs text-slate-500">将凭证设置为节点环境变量 KNOWRA_REC_WORKER_TOKEN；启动方式见工程 docs/RECOMMENDATION-RUNBOOK.md。凭证不会写入浏览器存储。</p>
          {data.workers.map(w => <div key={w.node_id} className="mt-3 flex justify-between"><span>{w.node_id}</span><button disabled={!!busy} className={button} onClick={() => void action('revoke', () => revokeRecommendationWorker(w.node_id))}>撤销节点</button></div>)}
        </details>
      </>}
    </div>
  </div>
}

function RecommendationCard({ item, batchId, busy, onAdopt, onError }: { item: PersonalRecItem; batchId: string; busy: boolean; onAdopt: () => void; onError: (e: unknown) => void }) {
  const ref = useRef<HTMLElement>(null)
  const sent = useRef(false)
  useEffect(() => {
    const observer = new IntersectionObserver(entries => {
      if (entries.some(e => e.isIntersecting) && !sent.current) {
        sent.current = true
        void recommendationEvent(batchId, item.arxiv_id, 'exposed').catch(() => { sent.current = false })
      }
    }, { threshold: 0.5 })
    if (ref.current) observer.observe(ref.current)
    return () => observer.disconnect()
  }, [batchId, item.arxiv_id])
  return <article ref={ref} className="rounded-xl border border-slate-800 bg-slate-900/30 p-5">
    <p className="mb-2 text-xs text-indigo-300">{lanes[item.lane]} · {item.historical ? '历史补漏' : '近期论文'} · {item.ai ? 'AI 精选' : '基础排序'}</p>
    <h2 className="font-semibold leading-relaxed">{item.title}</h2>
    <p className="mt-1 text-xs text-slate-500">{item.authors.slice(0, 3).join(', ')} · {item.published?.slice(0, 10)}</p>
    <p className="mt-3 text-sm leading-relaxed text-indigo-100">{item.reason}</p>
    {item.sources.length > 0 && <p className="mt-2 text-xs text-slate-500">关联库内论文：{item.sources.map(s => s.title).join('；')}</p>}
    <details className="mt-4 text-sm" onToggle={e => { if (e.currentTarget.open) void recommendationEvent(batchId, item.arxiv_id, 'viewed').catch(onError) }}><summary className="cursor-pointer text-slate-400">查看摘要与依据</summary><p className="mt-3 whitespace-pre-wrap leading-relaxed text-slate-300">{item.abstract || '暂无摘要'}</p>{item.evidence && <blockquote className="mt-3 border-l-2 border-indigo-500 pl-3 text-slate-400">{item.evidence}</blockquote>}</details>
    <div className="mt-4 flex gap-3"><button className={button} disabled={busy} onClick={onAdopt}>加入知识库</button><a href={`https://arxiv.org/abs/${item.arxiv_id}`} target="_blank" rel="noreferrer" className="py-1.5 text-sm text-slate-400">arXiv ↗</a></div>
  </article>
}
