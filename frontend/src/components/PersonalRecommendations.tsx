import { getFormattingLocale } from '../i18n/store'
import { t as tr } from '../i18n/catalog'
import { useLocale } from '../i18n/preferences'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Check, Loader2 } from 'lucide-react'
import { personalRecommendations, saveRecommendationFocus, refreshPersonalRecommendations, recommendationEvent, recommendationHistory, recommendationStorage, cleanupRecommendationStorage,
  PersonalRecommendationsUnavailableError, type PersonalFeed, type PersonalRecItem, type RecommendationBatch, type RecommendationStorage } from '../api/recommendations'
import { importRecommendation, localRecommendationWorker, startWorkspaceRecommendationWorker, stopLocalRecommendationWorker } from '../api/client'

const labels: Record<string, string> = { get domain() { return tr("领域") }, get problem() { return tr("研究问题") }, get method() { return tr("方法") }, get dataset() { return tr("数据集") }, get team() { return tr("团队") } }
const workerHealth: Record<string, string> = { get ready() { return tr("等待任务") }, get busy() { return tr("正在生成") }, get model_unavailable() { return tr("AI 暂不可用") }, get failed() { return tr("任务失败") } }
const lanes = { get long_term() { return tr("长期兴趣") }, get recent() { return tr("当前课题") }, get explore() { return tr("相邻探索") } }
const paperCategories: Record<string, string> = {
  get 'cs.AI'() { return tr("人工智能") }, get 'cs.CL'() { return tr("自然语言处理") }, get 'cs.CV'() { return tr("计算机视觉") },
  get 'cs.LG'() { return tr("机器学习") }, get 'cs.RO'() { return tr("机器人") }, get 'cs.IR'() { return tr("信息检索") },
  get 'cs.DC'() { return tr("分布式计算") }, get 'stat.ML'() { return tr("统计机器学习") },
}
const button = 'rounded-lg border border-slate-700 px-3 py-1.5 text-sm hover:bg-slate-800 disabled:opacity-50'
const addButton = 'inline-flex items-center gap-2 rounded-lg border border-emerald-600 bg-emerald-700 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-emerald-600 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-400 disabled:cursor-not-allowed disabled:opacity-50'
function errorMessage(error: unknown) {
  const value = error as { response?: { data?: { detail?: unknown } }; message?: string }
  const detail = value.response?.data?.detail
  return typeof detail === 'string' ? detail : value.message || tr("操作失败，请重试")
}

export default function PersonalRecommendations({ onBrowseAll }: { onBrowseAll: () => void }) {
  useLocale()
  const [data, setData] = useState<PersonalFeed | null>(null)
  const [selectedBatchId, setSelectedBatchId] = useState('')
  const selectedBatch = useRef('')
  const requestVersion = useRef(0)
  const [focus, setFocus] = useState('')
  const initialized = useRef(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [unavailable, setUnavailable] = useState(false)
  const [localError, setLocalError] = useState('')
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
    const version = ++requestVersion.current
    try {
      const next = await personalRecommendations(selectedBatch.current || undefined)
      if (version === requestVersion.current) acceptFeed(next)
    } catch (error) {
      if (version === requestVersion.current) reportFeedError(error)
      throw error
    }
  }, [acceptFeed, reportFeedError])
  const loadLocalWorker = useCallback(async () => {
    try {
      setLocalRunning((await localRecommendationWorker()).running)
      setLocalError('')
    } catch {
      setLocalError(tr("暂时无法连接本机节点管理，请启动或重启新版桌面后端。已有精选仍可查看。"))
    }
  }, [])
  useEffect(() => {
    const refresh = () => {
      void load().catch(() => {})
      void loadLocalWorker()
    }
    refresh()
    const timer = window.setInterval(refresh, 60000)
    return () => clearInterval(timer)
  }, [load, loadLocalWorker])

  async function action(name: string, fn: () => Promise<unknown>) {
    setBusy(name); setError(''); setNotice('')
    try { await fn(); await load(); await loadLocalWorker() } catch (e) { setError(errorMessage(e)) } finally { setBusy('') }
  }

  function selectBatch(id: string) {
    selectedBatch.current = id
    setSelectedBatchId(id)
    void action('history', async () => {})
  }

  async function adopt(item: { arxiv_id: string; title: string; authors?: string[] }, batchId: string) {
    await recommendationEvent(batchId, item.arxiv_id, 'requested')
    await importRecommendation(item)
    // Confirm success immediately, even if refreshing the feed subsequently fails.
    requestVersion.current++
    setData(previous => previous ? {
      ...previous,
      items: previous.items.map(entry => entry.arxiv_id === item.arxiv_id ? { ...entry, in_library: true } : entry),
      pending_imports: previous.pending_imports.filter(entry => entry.arxiv_id !== item.arxiv_id),
    } : previous)
    setNotice(tr("已添加至知识库，正在更新本机采纳反馈。"))
  }

  return <div className="h-full overflow-y-auto bg-[var(--surface-0b0d12)] p-6 text-slate-200">
    <div className="mx-auto max-w-5xl space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div><h1 className="text-xl font-semibold">{tr("推荐精选")}</h1><p className="mt-1 text-sm text-slate-400">{tr("基于本地知识库，每周一、三、五精选最多 10 篇。无需云端登录，入库反馈保存在本机。")}</p></div>
        <button className={button} disabled={!!busy || !data || unavailable} onClick={() => void action('refresh', async () => {
          await startWorkspaceRecommendationWorker()
          const result = await refreshPersonalRecommendations()
          setNotice(result.status === 'empty_library' ? tr("请先将论文加入本地知识库。") : tr("已提交本机精选任务，正在使用本地知识库生成。"))
        })}>{busy === 'refresh' ? tr("提交中…") : tr("更新精选")}</button>
      </header>
      {unavailable && <section role="status" className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-5">
        <h2 className="font-medium text-amber-100">{tr("本机推荐服务尚未加载")}</h2>
        <p className="mt-2 text-sm leading-relaxed text-slate-300">{tr("请重启新版桌面后端以加载本地推荐接口。画像、任务和采纳记录均保存在本机，无需登录云端。")}</p>
        <p className="mt-2 text-xs text-slate-400">{tr("重启后服务会自动启动推荐节点；电脑关闭时暂停，重新打开后继续。")}</p>
        <div className="mt-4 flex gap-3"><button className={button} onClick={onBrowseAll}>{tr("查看完整推荐")}</button><button className={button} disabled={!!busy} onClick={() => void action('retry', load)}>{tr("重新检查服务")}</button></div>
      </section>}
      {error && !unavailable && <div role="alert" className="rounded-xl border border-rose-500/40 bg-rose-500/10 p-3 text-sm text-rose-200">{error}<button className="ml-3 underline" onClick={() => void action('retry', load)}>{tr("重试")}</button></div>}
      {notice && <p role="status" className="text-sm text-indigo-200">{notice}</p>}
      {!data && !error && <p>{tr("正在读取个人推荐…")}</p>}
      {data && !unavailable && <>
        <section className="rounded-xl border border-slate-800 bg-slate-900/40 p-4 text-sm">
          <div className="flex flex-wrap justify-between gap-2">
            <span className={data.worker_status === 'online' ? 'text-emerald-300' : 'text-amber-200'}>
              {data.worker_status === 'online' ? tr("执行节点在线") : data.worker_status === 'offline' ? tr("执行节点离线 · 新精选等待恢复后生成") : tr("尚未连接执行节点 · 请在下方设置")}</span>
            <span className="text-slate-400">{tr("最近精选：")}{' '}{data.latest_batch?.completed_at ? new Date(data.latest_batch.completed_at).toLocaleString(getFormattingLocale()) : tr("尚未生成")}</span>
          </div>
          {data.workers.map(w => <p key={w.node_id} className="mt-1 text-xs text-slate-400">{w.node_id === 'desktop-local' ? tr("本机") : w.node_id} · {workerHealth[w.health] || w.health} {tr("· 最近在线")}{' '}{w.last_seen_at ? new Date(w.last_seen_at).toLocaleString(getFormattingLocale()) : tr("尚未连接")}</p>)}
          {data.job && data.job.status !== 'completed' && <p className="mt-2 text-slate-400">{tr("当前任务：")}{' '}{{ queued: tr("等待执行"), running: tr("正在生成"), failed: tr("执行失败") }[data.job.status] || data.job.status} {data.job.error && `· ${data.job.error}`}</p>}
        </section>
        <details className="rounded-xl border border-slate-800 p-4">
          <summary className="cursor-pointer">{tr("科研品味 ·")}{' '}{data.profile.paper_count} {tr("篇论文 · 版本")}{' '}{data.profile.version}</summary>
          <p className="mt-3 text-xs leading-relaxed text-slate-500">{tr("根据库内论文已有的分类、研究问题和知识节点汇总，按画像权重展示。")}</p>
          <dl className="mt-3 space-y-3 text-sm text-slate-400">{Object.entries(data.profile.dimensions).map(([dimension, values]) => {
            const entries = Object.keys(values).slice(0, 10)
            return <div key={dimension} className="grid gap-x-4 gap-y-1 sm:grid-cols-[5rem_minmax(0,1fr)]">
              <dt className="leading-relaxed text-slate-200">{labels[dimension]}：</dt>
              <dd className="min-w-0 leading-relaxed">{!entries.length ? tr("暂无足够信息") : dimension === 'problem'
                ? <ul className="list-disc space-y-1.5 pl-5">{entries.map(entry => <li key={entry}>{entry.replace(/[。．.;；、，,\s]+$/u, '')}</li>)}</ul>
                : entries.join('、')}</dd>
            </div>
          })}</dl>
          <label className="mt-4 block text-sm" htmlFor="rec-focus">{tr("当前课题（可选，留空时从近期入库自动推断）")}</label>
          <textarea id="rec-focus" value={focus} maxLength={2000} onChange={e => setFocus(e.target.value)} rows={2} className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-950 p-3" placeholder={tr("例如：关注低成本三维重建中的泛化方法")} />
          <button className={button} disabled={!!busy} onClick={() => void action('focus', () => saveRecommendationFocus(focus))}>{tr("保存课题")}</button>
        </details>
        {data.pending_imports.length > 0 && <section className="rounded-xl border border-indigo-500/30 p-4"><h2 className="font-medium">{tr("待入库请求")}</h2><p className="mt-1 text-xs text-slate-400">{tr("尚未完成的本机入库请求保留在这里，实际入库成功后才计入采纳。")}</p>{data.pending_imports.map(item => <div key={item.arxiv_id} className="mt-3 flex items-center gap-3 text-sm"><span className="flex-1">{item.title}</span><button className={addButton} disabled={!!busy} onClick={() => void action(item.arxiv_id, () => adopt(item, item.batch_id))}>{busy === item.arxiv_id ? tr("入库中…") : tr("完成入库")}</button></div>)}</section>}
        <HistoryPicker selected={selectedBatchId} latest={data.latest_batch} disabled={!!busy} onSelect={selectBatch} />
        {busy === 'history' ? <p role="status" className="text-sm text-slate-400">{tr("正在读取该期精选…")}</p> : selectedBatchId && data.batch?.id !== selectedBatchId ? <p className="text-sm text-slate-400">{tr("该期精选暂时无法读取，请重试或返回最新一期。")}</p> : <>
        {data.batch && <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-slate-400">
          <p>{selectedBatchId ? batchLabel(data.batch) : tr("最新一期")} · {data.batch.count} {tr("篇")}{' '}{selectedBatchId ? tr(" · 当时科研品味版本 {0}", { 0: data.batch.profile_version ?? tr('未知') }) : ''}</p>
        </div>}
        {data.batch?.error && <p role="status" className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 text-sm text-amber-200">{tr("本期生成说明：")}{' '}{data.batch.error === tr('基础排序：模型暂不可用') ? tr("本期 AI 精排未完成，已使用基础排序（旧批次未记录具体原因）") : data.batch.error}</p>}
        {selectedBatchId && <p className="text-xs text-slate-500">{tr("保留本期生成时的论文顺序和推荐理由，入库状态按当前知识库更新。")}</p>}
        {!data.items.length && <div className="rounded-xl border border-dashed border-slate-700 p-10 text-center text-slate-400">{data.batch ? tr("本期没有符合条件的精选，相关性不足时不会凑数。") : data.profile.paper_count ? tr("还没有符合条件的精选。连接执行节点后更新；相关性不足时不会凑数。") : tr("先加入本地论文，让推荐从你的知识库开始。")}</div>}
        <div className="grid grid-cols-1 gap-4">{data.items.map(item => <RecommendationCard key={`${data.batch?.id}:${item.arxiv_id}`} item={item} batchId={data.batch!.id} busy={!!busy} adding={busy === item.arxiv_id}
          onError={e => setError(errorMessage(e))} onAdopt={() => void action(item.arxiv_id, () => adopt(item, data.batch!.id))} />)}</div>
        </> }
        <p className="text-xs text-slate-500">{tr("已浏览")}{' '}{data.metrics.viewed} {tr("篇 · 已采纳")}{' '}{data.metrics.adopted} {tr("篇 · 14 天采纳率")}{' '}{data.metrics.adoption_rate_14d === null ? tr("等待观察窗口完成") : `${Math.round(data.metrics.adoption_rate_14d * 100)}%`}{tr("（成熟样本")}{' '}{data.metrics.mature_viewed}{tr("，目标 30%）。未采纳不会记为不喜欢。")}</p>
        <RecommendationStoragePanel />
        <details className="rounded-xl border border-slate-800 p-4 text-sm">
          <summary className="cursor-pointer">{tr("本机执行节点与调用预算")}</summary>
          <p className="my-3 text-slate-400">{tr("月度付费调用预算 ¥")}{' '}{data.budget.limit_cny}{tr("；已预留上界 ¥")}{' '}{data.budget.reserved_cny.toFixed(2)}{tr("。CLI 费用未知时不显示为零费用。节点离线仅在站内提醒。")}</p>
          <p className="mb-3 text-slate-400">{tr("推荐随本机后端启动，直接使用本地知识库和 SQLite 队列，不依赖云端。电脑关闭时暂停，重新打开后自动恢复。")}</p>
          {localError && <p role="status" className="mb-3 text-amber-200">{localError}</p>}
          <div className="mb-4 flex gap-2">
            <button className={button} disabled={!!busy || localRunning || !!localError} onClick={() => void action('local-start', async () => {
              await startWorkspaceRecommendationWorker()
              setNotice(tr("本机节点已启动，使用 Codex CLI。关闭桌面后端或电脑后任务会等待节点恢复。"))
            })}>{localRunning ? tr("本机节点运行中") : tr("在本机启动节点")}</button>
            <button className={button} disabled={!!busy || !localRunning} onClick={() => void action('local-stop', stopLocalRecommendationWorker)}>{tr("停止本机节点")}</button>
          </div>
          <p className="mt-2 text-xs text-slate-500">{tr("以后可将本地服务与推荐数据迁移到 SSH 主机。推荐默认使用 Codex CLI；修改模型后停止并重新启动节点即可生效。")}</p>
        </details>
      </>}
    </div>
  </div>
}

function RecommendationCard({ item, batchId, busy, adding, onAdopt, onError }: { item: PersonalRecItem; batchId: string; busy: boolean; adding: boolean; onAdopt: () => void; onError: (e: unknown) => void }) {
  useLocale()
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
    <h2 className="font-semibold leading-relaxed">{item.title}</h2>
    <p className="mt-1 text-xs text-slate-500">{item.authors.slice(0, 3).join(', ')} · {item.published?.slice(0, 10)}</p>
    <div className="mt-4 text-sm leading-7">
      <p className="mb-1 text-xs font-medium text-indigo-300">{tr("推荐理由")}</p>
      <p className="whitespace-pre-line text-indigo-100">{item.reason}</p>
    </div>
    {item.sources.length > 0 && <p className="mt-2 text-xs text-slate-500">{tr("关联库内论文：")}{' '}{item.sources.map(s => s.title).join('；')}</p>}
    <details className="mt-4 text-sm" onToggle={e => { if (e.currentTarget.open) void recommendationEvent(batchId, item.arxiv_id, 'viewed').catch(onError) }}><summary className="cursor-pointer text-slate-400">{tr("查看摘要与依据")}</summary><p className="mt-3 whitespace-pre-wrap leading-relaxed text-slate-300">{item.abstract || tr("暂无摘要")}</p>{item.evidence && <blockquote className="mt-3 border-l-2 border-indigo-500 pl-3 text-slate-400">{item.evidence}</blockquote>}</details>
    <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-3">
      {item.in_library
        ? <span role="status" className="inline-flex items-center gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-1.5 text-sm font-medium text-emerald-300"><Check size={16} aria-hidden="true" />{tr("已添加至知识库")}</span>
        : <button className={addButton} disabled={busy} aria-busy={adding} onClick={onAdopt}>{adding && <Loader2 size={16} className="animate-spin" aria-hidden="true" />}{adding ? tr("正在添加…") : tr("加入知识库")}</button>}
      <a href={`https://arxiv.org/abs/${item.arxiv_id}`} target="_blank" rel="noreferrer" className="py-1.5 text-sm text-slate-400">arXiv ↗</a>
      <p className="text-xs text-indigo-300">{lanes[item.lane]} · {item.historical ? tr("历史补漏") : tr("近期论文")} · {item.ai ? tr("AI 精选") : tr("基础排序")}</p>
      <span className="rounded-md border border-slate-700/70 px-2 py-1 text-xs text-slate-400" title={item.primary_category ? tr("arXiv 分类：{0}", { 0: item.primary_category }) : tr("论文元数据暂无类别")}>{tr("类别：")}{' '}{item.primary_category ? paperCategories[item.primary_category] || item.primary_category : tr("未分类")}</span>
    </div>
  </article>
}


function batchLabel(batch: RecommendationBatch) {
  const manual = batch.slot.startsWith('manual:')
  const date = manual ? `${batch.slot.slice(7)}T09:00:00+08:00` : batch.slot
  const label = new Date(date).toLocaleDateString(getFormattingLocale(), { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short' })
  return `${label} · ${manual ? tr("手动更新") : tr("定时精选")}`
}

function HistoryPicker({ selected, latest, disabled, onSelect }: {
  selected: string; latest: RecommendationBatch | null; disabled: boolean; onSelect: (id: string) => void
}) {
  useLocale()
  const [batches, setBatches] = useState<RecommendationBatch[]>([])
  const [retentionDays, setRetentionDays] = useState(90)
  const [nextOffset, setNextOffset] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const version = useRef(0)
  const fetchHistory = useCallback(async (offset = 0) => {
    const request = ++version.current
    setLoading(true); setError('')
    try {
      const result = await recommendationHistory(offset)
      if (request !== version.current) return
      setBatches(previous => {
        const merged = offset ? [...previous, ...result.batches] : result.batches
        return [...new Map(merged.map(batch => [batch.id, batch])).values()]
      })
      setRetentionDays(result.retention_days)
      setNextOffset(result.next_offset)
    } catch (e) { if (request === version.current) setError(errorMessage(e)) }
    finally { if (request === version.current) setLoading(false) }
  }, [])
  useEffect(() => {
    let cancelled = false
    const request = ++version.current
    void recommendationHistory().then(result => {
      if (cancelled || request !== version.current) return
      setBatches(result.batches); setNextOffset(result.next_offset); setRetentionDays(result.retention_days); setError('')
    }).catch(e => {
      if (!cancelled && request === version.current) setError(errorMessage(e))
    }).finally(() => {
      if (!cancelled && request === version.current) setLoading(false)
    })
    return () => { cancelled = true }
  }, [latest?.id])
  return <section className="rounded-xl border border-slate-800 p-4 text-sm">
    <div className="flex flex-wrap items-center gap-3">
      <label htmlFor="rec-history" className="font-medium">{tr("历史精选")}</label>
      <select id="rec-history" value={selected} disabled={disabled} onChange={e => onSelect(e.target.value)} className="min-w-0 max-w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2">
        <option value="">{tr("最新一期")}</option>
        {selected && !batches.some(batch => batch.id === selected) && <option value={selected}>{tr("当前查看的历史批次")}</option>}
        {batches.map(batch => <option key={batch.id} value={batch.id}>{batchLabel(batch)} · {batch.count} {tr("篇")}</option>)}
      </select>
      {selected && <button className={button} disabled={disabled} onClick={() => onSelect('')}>{tr("返回最新一期")}</button>}
      {nextOffset !== null && <button className={button} disabled={loading} onClick={() => void fetchHistory(nextOffset)}>{tr("加载更早批次")}</button>}
      {loading && <span role="status" className="text-slate-400">{tr("正在加载历史…")}</span>}
    </div>
    <p className="mt-2 text-xs text-slate-500">{tr("可回看最近")}{' '}{retentionDays} {tr("天的精选，按北京时间显示日期。超期后移出历史入口，已入库论文和采纳反馈继续保留。")}</p>
    {error && <p role="alert" className="mt-2 text-rose-200">{error}<button className="ml-3 underline" onClick={() => void fetchHistory()}>{tr("重试")}</button></p>}
  </section>
}


function formatBytes(bytes: number) {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(2)} MB` : `${(bytes / 1024).toFixed(1)} KB`
}

function RecommendationStoragePanel() {
  useLocale()
  const [preview, setPreview] = useState<RecommendationStorage | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  async function inspect() {
    setBusy(true); setError(''); setMessage('')
    try { setPreview(await recommendationStorage()) }
    catch (e) { setError(errorMessage(e)) }
    finally { setBusy(false) }
  }
  async function clean() {
    if (!preview || !window.confirm(tr("清理超过 {0} 天的推荐缓存？将重新核对过期范围，保留知识库 PDF、采纳反馈和待入库请求。清理后无法回看被删除的推荐缓存。", { 0: preview.retention_days }))) return
    setBusy(true); setError(''); setMessage('')
    try {
      const result = await cleanupRecommendationStorage()
      setMessage(tr("已清理 {0} 期推荐和 {1} 条候选缓存。{2}", { 0: result.deleted_batches, 1: result.deleted_candidates, 2: result.compacted ? `已整理数据库，释放 ${formatBytes(result.reclaimed_bytes)}。` : tr('数据已清理，数据库正在使用，空闲空间将供后续写入复用；可稍后再次整理。') }))
      setPreview(await recommendationStorage())
    } catch (e) { setError(errorMessage(e)) }
    finally { setBusy(false) }
  }
  return <details className="rounded-xl border border-slate-800 p-4 text-sm">
    <summary className="cursor-pointer">{tr("推荐存储管理")}</summary>
    <p className="mt-3 text-slate-400">{tr("清理过期的标题、摘要及推荐缓存，并整理数据库空间。知识库中的 PDF 和采纳反馈继续保留。")}</p>
    <button className={`${button} mt-3`} disabled={busy} onClick={() => void inspect()}>{busy ? tr("处理中…") : tr("检查可清理空间")}</button>
    {preview && <div className="mt-3 space-y-2 text-slate-400">
      <p>{tr("推荐数据库占用")}{' '}{formatBytes(preview.database_bytes)}{tr("；超过")}{' '}{preview.retention_days} {tr("天的可清理数据：")}{' '}{preview.expired_batches} {tr("期推荐、")}{' '}{preview.expired_candidates} {tr("条候选，内容约")}{' '}{formatBytes(preview.estimated_payload_bytes)}{tr("（实际释放以整理结果为准）。")}</p>
      {preview.protected_batches > 0 && <p>{preview.protected_batches} {tr("期历史关联阅读、采纳反馈或入库请求，保留用于追溯。")}</p>}
      <button className={button} disabled={busy} onClick={() => void clean()}>{tr("一键清理过期缓存并整理空间")}</button>
    </div>}
    {message && <p role="status" className="mt-3 text-emerald-300">{message}</p>}
    {error && <p role="alert" className="mt-3 text-rose-200">{error}</p>}
  </details>
}
