import { getFormattingLocale } from '../i18n/store'
import { t as tr, matchesMessage } from '../i18n/catalog'
import { useLocale } from '../i18n/preferences'
import { useEffect, useState, useRef, useMemo, useCallback, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import {
  CheckCircle2, XCircle, Clock, RefreshCw, Play, FileText,
  RotateCw, Loader2, Search, LayoutGrid, List as ListIcon, AlertTriangle,
  PanelRightOpen, PanelRightClose, Pencil, Upload, ChevronDown, MoreVertical, Trash2,
} from 'lucide-react'
import {
  listPapers, processAll, processPaper, retryPaper, retryFailedPapers, reprocessPaper, firstPageUrl, getStatus,
  uploadPapers, listPaperCategories, updatePaperCategory, deletePaper,
  getCachedPapers,
  type FileSearchConfirmation,
  type PaperRecord,
} from '../api/client'
import { CANCELLED_EXTRACTION_NOTICE } from '../api/paperExtractConfirm'
import { useFileSearchConfirmation } from '../hooks/useFileSearchConfirmation'
import { useDismissOnOutside } from '../hooks/useDismissOnOutside'
import { useAnchoredMenu } from '../hooks/useAnchoredMenu'
import PromptPanel from '../components/PromptPanel'
import TaskNotice, { type TaskNoticeTone } from '../components/TaskNotice'
import PaperProcessBadge from '../components/PaperProcessBadge'
import { inferPaperProcessMeta, paperCardProcessAction, summarizePaperError } from '../components/paperProcessMeta'
import { PAPER_CATEGORIES_CHANGED_EVENT } from '../lib/paperCategoryEvents'
import {
  createPaperDeleteSuccessNoticeController,
  isCurrentDeleteSuccessNotice,
} from './paperDeleteSuccessNotice'

interface ProcStatus {
  running: boolean
  total: number
  done: number
  errors: number
  current: string
}

type ViewMode = 'grid' | 'list'
type Filter = 'all' | 'processed' | 'pending' | 'failed'

const CATEGORY_INHERIT = '__inherit__'
const CATEGORY_FILTER_ALL = '__all__'
const DEFAULT_PAPER_CATEGORIES = ['LLM', 'VLM', 'VLA', '三维重建-静态', '三维重建-动态', '世界模型', '其他']
const CATEGORY_DOT_COLORS = [
  'bg-violet-400',
  'bg-sky-400',
  'bg-cyan-400',
  'bg-emerald-400',
  'bg-amber-400',
  'bg-rose-400',
  'bg-indigo-400',
  'bg-teal-400',
  'bg-fuchsia-400',
  'bg-lime-400',
  'bg-orange-400',
  'bg-blue-400',
]
const CATEGORY_DOT_BY_NAME: Record<string, string> = {
  LLM: CATEGORY_DOT_COLORS[0],
  VLM: CATEGORY_DOT_COLORS[1],
  VLA: CATEGORY_DOT_COLORS[2],
  '三维重建-静态': CATEGORY_DOT_COLORS[6],
  '三维重建-动态': CATEGORY_DOT_COLORS[3],
  世界模型: CATEGORY_DOT_COLORS[4],
  蒸馏: CATEGORY_DOT_COLORS[5],
  表征学习: CATEGORY_DOT_COLORS[8],
  动态重建: CATEGORY_DOT_COLORS[7],
}

interface ActionNotice {
  tone: TaskNoticeTone
  title: string
  detail?: string
  autoDismissId?: number
}

interface PapersPageProps {
  onOpenReview: (paperId: number) => void
}

export default function PapersPage({ onOpenReview }: PapersPageProps) {
  useLocale()
  const { confirmIfNeeded } = useFileSearchConfirmation()
  const [papers, setPapers] = useState<PaperRecord[]>(() => getCachedPapers() ?? [])
  const [loading, setLoading] = useState(() => getCachedPapers() === null)
  const [selected, setSelected] = useState<PaperRecord | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<PaperRecord | null>(null)
  const [deleteBusy, setDeleteBusy] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [pendingIds, setPendingIds] = useState<Set<number>>(new Set())
  const [status, setStatus] = useState<ProcStatus | null>(null)
  const [view, setView] = useState<ViewMode>('grid')
  const [filter, setFilter] = useState<Filter>('all')
  const [categoryFilter, setCategoryFilter] = useState(CATEGORY_FILTER_ALL)
  const [search, setSearch] = useState('')
  const [actionNotice, setActionNotice] = useState<ActionNotice | null>(null)
  const [bulkProcessingPending, setBulkProcessingPending] = useState(false)
  const [bulkRetrying, setBulkRetrying] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [pendingFiles, setPendingFiles] = useState<File[]>([])
  const [categoryOptions, setCategoryOptions] = useState<string[]>(DEFAULT_PAPER_CATEGORIES)
  const [categoryDrafts, setCategoryDrafts] = useState<Record<number, string>>({})
  const [categorySavingIds, setCategorySavingIds] = useState<Set<number>>(new Set())
  const fileInputRef = useRef<HTMLInputElement>(null)
  // Global Prompt column collapses to a thin icon strip by default so
  // the paper grid gets the whole horizontal width. Most users don't
  // edit the extraction prompt every visit; surface it on demand.
  const [promptOpen, setPromptOpen] = useState(false)
  const wasRunningRef = useRef(false)
  const deleteSuccessNotice = useRef(createPaperDeleteSuccessNoticeController())

  useEffect(() => () => {
    deleteSuccessNotice.current.dispose()
  }, [])

  const requestExtractConfirmation = useCallback(async (): Promise<FileSearchConfirmation | false | undefined> => {
    const decision = await confirmIfNeeded()
    if (decision.status === 'cancelled') {
      setActionNotice({ tone: 'info', title: tr(CANCELLED_EXTRACTION_NOTICE) })
      return false
    }
    if (decision.status === 'blocked') {
      setActionNotice({ tone: 'error', title: tr("处理启动失败"), detail: decision.message })
      return false
    }
    return decision.status === 'confirmed' ? decision.confirmation : undefined
  }, [confirmIfNeeded])

  const load = useCallback(async () => {
    try {
      const ps = await listPapers()
      setPapers(ps)
      setSelected(prev => prev ? ps.find(p => p.id === prev.id) || null : null)
      setActionNotice(previous => matchesMessage(previous?.title, "论文列表暂未刷新") ? null : previous)
    } catch (error) {
      console.error('Failed to load papers', error)
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    let cancelled = false
    const loadInitialPapers = async () => {
      try {
        const ps = await listPapers()
        if (cancelled) return
        setPapers(ps)
        setSelected(prev => prev ? ps.find(p => p.id === prev.id) || null : null)
      } catch (error) {
        console.error('Failed to load papers', error)
        if (!cancelled) setActionNotice({ tone: 'warning', title: tr("论文列表暂未刷新"), detail: tr("保留已加载的论文；后端响应后会自动更新。") })
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    void loadInitialPapers()
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    let cancelled = false
    const refreshCategories = (reloadPapers = false) => {
      listPaperCategories()
        .then(items => {
          if (!cancelled && items.length > 0) setCategoryOptions(items.map(item => item.name))
        })
        .catch(error => console.error('Failed to load paper categories', error))
      if (reloadPapers) void load()
    }
    refreshCategories()
    const onCategoriesChanged = () => refreshCategories(true)
    window.addEventListener(PAPER_CATEGORIES_CHANGED_EVENT, onCategoriesChanged)
    return () => {
      cancelled = true
      window.removeEventListener(PAPER_CATEGORIES_CHANGED_EVENT, onCategoriesChanged)
    }
  }, [load])

  useEffect(() => {
    let cancelled = false
    let polling = false
    let lastProgress = ''
    let lastListRefresh = 0
    const poll = async () => {
      if (polling) return
      polling = true
      try {
        const s = await getStatus()
        if (cancelled) return
        setStatus(s)
        setActionNotice(previous => matchesMessage(previous?.title, "状态暂未刷新") ? null : previous)
        if (wasRunningRef.current && !s.running) {
          setPendingIds(new Set())
          setActionNotice(
            s.errors > 0
              ? {
                  tone: 'warning',
                  title: tr("处理结束，{0} 篇论文失败", { 0: s.errors }),
                  detail: tr("可切换到“失败”筛选并逐条重试。"),
                }
              : {
                  tone: 'success',
                  title: tr("处理完成"),
                  detail: tr("所有任务已处理完成。"),
                },
          )
          await load()
        } else {
          const progress = `${s.running}:${s.current}:${s.done}:${s.errors}`
          if (progress !== lastProgress || Date.now() - lastListRefresh > 30000) {
            await load()
            lastProgress = progress
            lastListRefresh = Date.now()
          }
        }
        wasRunningRef.current = s.running
      } catch (error) {
        if (cancelled) return
        console.error('Failed to poll processing status', error)
        setActionNotice({
          tone: 'warning',
          title: tr("状态暂未刷新"),
          detail: tr("暂时无法连接后端，正在自动重试；这不代表后台论文任务失败。"),
        })
      } finally {
        polling = false
      }
    }
    void poll()
    const id = setInterval(poll, 1500)
    return () => { cancelled = true; clearInterval(id) }
  }, [load])

  const handleProcessOne = async (p: PaperRecord) => {
    setActionNotice(null)
    const confirmation = await requestExtractConfirmation()
    if (confirmation === false) return
    setPendingIds(prev => new Set(prev).add(p.id))
    try {
      await processPaper(p.id, confirmation)
      await load()
      setActionNotice({
        tone: 'info',
        title: tr("已提交处理：{0}", { 0: p.filename }),
        detail: tr("可在页面顶部看到整体进度，完成后会自动刷新列表。"),
      })
    } catch (error) {
      setActionNotice({
        tone: 'error',
        title: tr("处理启动失败：{0}", { 0: p.filename }),
        detail: getErrorMessage(error),
      })
    } finally {
      setPendingIds(prev => { const n = new Set(prev); n.delete(p.id); return n })
    }
  }

  const handleRetry = async (p: PaperRecord) => {
    setActionNotice(null)
    const confirmation = await requestExtractConfirmation()
    if (confirmation === false) return
    setPendingIds(prev => new Set(prev).add(p.id))
    try {
      await retryPaper(p.id, confirmation)
      await load()
      setActionNotice({
        tone: 'info',
        title: tr("已提交重试：{0}", { 0: p.filename }),
        detail: tr("系统会清理该论文错误状态并重新进入处理队列。"),
      })
    } catch (error) {
      setActionNotice({
        tone: 'error',
        title: tr("重试启动失败：{0}", { 0: p.filename }),
        detail: getErrorMessage(error),
      })
    } finally {
      setPendingIds(prev => { const n = new Set(prev); n.delete(p.id); return n })
    }
  }

  const handleReprocess = async (p: PaperRecord) => {
    setActionNotice(null)
    const confirmation = await requestExtractConfirmation()
    if (confirmation === false) return
    const ok = confirm(tr("确认重新处理这篇论文？现有抽取结果和图谱节点会被清空，并重新调用大模型。"))
    if (!ok) return

    setPendingIds(prev => new Set(prev).add(p.id))
    try {
      await reprocessPaper(p.id, confirmation)
      setActionNotice({
        tone: 'info',
        title: tr("已提交重新处理：{0}", { 0: p.filename }),
        detail: tr("旧抽取结果已清空，等待后端重新生成结构化结果。"),
      })
    } catch (error) {
      setActionNotice({
        tone: 'error',
        title: tr("重新处理启动失败：{0}", { 0: p.filename }),
        detail: getErrorMessage(error),
      })
    } finally {
      setPendingIds(prev => { const n = new Set(prev); n.delete(p.id); return n })
    }
  }

  const handleRetryFailedAll = async () => {
    const failedPapers = papers.filter(p => p.error && !p.processed)
    if (failedPapers.length === 0) {
      setActionNotice({
        tone: 'info',
        title: tr("当前没有失败论文可重试"),
      })
      return
    }

    const confirmation = await requestExtractConfirmation()
    if (confirmation === false) return
    const ok = confirm(
      tr("确认重试全部 {0} 篇失败论文？这会清空它们当前的错误状态和 OpenAI 缓存，并重新调用大模型。", { 0: failedPapers.length }),
    )
    if (!ok) return

    const failedIds = failedPapers.map(p => p.id)
    setBulkRetrying(true)
    setPendingIds(prev => new Set([...prev, ...failedIds]))
    setActionNotice(null)
    try {
      const result = await retryFailedPapers(confirmation)
      if ((result.retried || 0) > 0) {
        setActionNotice({
          tone: 'info',
          title: tr("已提交批量重试：{0} 篇失败论文", { 0: result.retried }),
          detail: tr("可在失败筛选里跟踪每篇论文状态变化。"),
        })
      } else {
        setPendingIds(prev => {
          const next = new Set(prev)
          failedIds.forEach(id => next.delete(id))
          return next
        })
        setActionNotice({
          tone: 'info',
          title: tr("当前没有失败论文可重试"),
        })
      }
    } catch (error) {
      setPendingIds(prev => {
        const next = new Set(prev)
        failedIds.forEach(id => next.delete(id))
        return next
      })
      setActionNotice({
        tone: 'error',
        title: tr("批量重试启动失败"),
        detail: getErrorMessage(error),
      })
    } finally {
      setBulkRetrying(false)
    }
  }

  const handleProcessPendingAll = async () => {
    const pendingPapers = papers.filter(p => !p.processed && !p.error)
    if (pendingPapers.length === 0) {
      setActionNotice({
        tone: 'info',
        title: tr("当前没有待处理论文"),
      })
      return
    }

    const confirmation = await requestExtractConfirmation()
    if (confirmation === false) return
    const ok = confirm(tr("确认处理全部 {0} 篇待处理论文？", { 0: pendingPapers.length }))
    if (!ok) return

    const pendingPaperIds = pendingPapers.map(p => p.id)
    setBulkProcessingPending(true)
    setPendingIds(prev => new Set([...prev, ...pendingPaperIds]))
    setActionNotice(null)
    try {
      await processAll(confirmation)
      setActionNotice({
        tone: 'info',
        title: tr("已提交批量处理：{0} 篇待处理论文", { 0: pendingPapers.length }),
        detail: tr("进度会在顶部状态条和每篇论文卡片中实时体现。"),
      })
    } catch (error) {
      setPendingIds(prev => {
        const next = new Set(prev)
        pendingPaperIds.forEach(id => next.delete(id))
        return next
      })
      setActionNotice({
        tone: 'error',
        title: tr("批量处理启动失败"),
        detail: getErrorMessage(error),
      })
    } finally {
      setBulkProcessingPending(false)
    }
  }

  // Step 1+2: the OS picker browses folders + multi-selects PDFs. We then
  // stage them for an explicit 确认 step rather than uploading immediately.
  const handleFilesSelected = (e: React.ChangeEvent<HTMLInputElement>) => {
    const fileList = e.target.files
    const files = fileList ? Array.from(fileList).filter(f => f.name.toLowerCase().endsWith('.pdf')) : []
    e.target.value = '' // reset so re-picking the same files fires onChange again
    if (files.length === 0) {
      setActionNotice({ tone: 'warning', title: tr("没有选择 PDF 文件") })
      return
    }
    setPendingFiles(files)
  }

  // Step 3: 确认 → copy the staged files into ./papers and register them.
  const handleConfirmUpload = async () => {
    if (pendingFiles.length === 0) return
    setUploading(true)
    setActionNotice(null)
    try {
      const r = await uploadPapers(pendingFiles)
      const parts = [tr("上传 {0} 篇", { 0: r.saved })]
      if (r.skipped_existing > 0) parts.push(tr("已存在跳过 {0} 篇", { 0: r.skipped_existing }))
      if (r.duplicates > 0) parts.push(tr("去重 {0} 篇", { 0: r.duplicates }))
      if (r.rejected.length > 0) parts.push(tr("无效 {0} 个", { 0: r.rejected.length }))
      setActionNotice({
        tone: r.saved > 0 ? 'success' : 'info',
        title: parts.join(' · '),
        detail:
          r.saved > 0
            ? tr("已拷贝到 ./papers 并登记为未处理，可在此直接处理。")
            : tr("所选文件都已存在或无效，未新增。"),
      })
      setPendingFiles([])
      await load()
    } catch (error) {
      setActionNotice({ tone: 'error', title: tr("上传失败"), detail: getErrorMessage(error) })
    } finally {
      setUploading(false)
    }
  }

  const isPending = (p: PaperRecord) =>
    pendingIds.has(p.id) || !!(status?.running && status.current === p.filename)

  const handlePaperClick = (paper: PaperRecord) => {
    if (paper.processed) {
      onOpenReview(paper.id)
      return
    }
    setSelected(paper)
  }

  const requestDeletePaper = (paper: PaperRecord) => {
    setDeleteError(null)
    setDeleteTarget(paper)
  }

  const cancelDeletePaper = () => {
    if (deleteBusy) return
    setDeleteTarget(null)
    setDeleteError(null)
  }

  const confirmDeletePaper = async () => {
    if (!deleteTarget || deleteBusy) return
    const target = deleteTarget
    setDeleteBusy(true)
    setDeleteError(null)
    try {
      const result = await deletePaper(target.id)
      setPapers(prev => prev.filter(item => item.id !== target.id))
      setSelected(prev => (prev?.id === target.id ? null : prev))
      setPendingIds(prev => {
        const next = new Set(prev)
        next.delete(target.id)
        return next
      })
      setCategoryDrafts(prev => {
        const next = { ...prev }
        delete next[target.id]
        return next
      })
      setDeleteTarget(null)
      deleteSuccessNotice.current.show(
        {
          title: result.already_deleted || result.status === 'already_deleted'
            ? tr("论文已删除")
            : tr("已永久删除「{0}」", { 0: target.title || target.filename }),
          detail: tr("原始 PDF（若位于资料库管理目录内）及相关抽取、Wiki、索引和引用已移除。共用概念已保留，将在下次编译时更新。"),
        },
        setActionNotice,
        id => setActionNotice(previous => isCurrentDeleteSuccessNotice(previous, id) ? null : previous),
      )
    } catch (error) {
      setDeleteError(getErrorMessage(error) || tr("删除失败，请重试"))
    } finally {
      setDeleteBusy(false)
    }
  }

  const handleCategoryChange = async (paper: PaperRecord, nextValue: string) => {
    const currentValue = paper.paper_category_override || CATEGORY_INHERIT
    if (nextValue === currentValue || categorySavingIds.has(paper.id)) return

    setCategoryDrafts(prev => ({ ...prev, [paper.id]: nextValue }))
    setCategorySavingIds(prev => new Set(prev).add(paper.id))
    try {
      const updated = await updatePaperCategory(
        paper.id,
        nextValue === CATEGORY_INHERIT ? null : nextValue,
      )
      setPapers(prev => prev.map(item => item.id === updated.id ? updated : item))
      setSelected(prev => prev?.id === updated.id ? updated : prev)
    } catch (error) {
      setActionNotice({
        tone: 'error',
        title: tr("论文大类保存失败：{0}", { 0: paper.title || paper.filename }),
        detail: getErrorMessage(error),
      })
    } finally {
      setCategoryDrafts(prev => {
        const next = { ...prev }
        delete next[paper.id]
        return next
      })
      setCategorySavingIds(prev => {
        const next = new Set(prev)
        next.delete(paper.id)
        return next
      })
    }
  }

  const stats = useMemo(() => ({
    processed: papers.filter(p => p.processed).length,
    failed: papers.filter(p => p.error && !p.processed).length,
    pending: papers.filter(p => !p.processed && !p.error).length,
  }), [papers])

  const recentFailures = useMemo(
    () =>
      papers
        .filter(p => p.error && !p.processed)
        .map(p => ({
          id: p.id,
          title: p.title || p.filename,
          summary: summarizePaperError(p.error) || tr("未知错误"),
        }))
        .slice(0, 3),
    [papers],
  )

  const categoryCounts = useMemo(() => {
    const counts = new Map<string, number>()
    papers.forEach(paper => {
      const category = effectivePaperCategoryLabel(paper)
      counts.set(category, (counts.get(category) || 0) + 1)
    })
    return counts
  }, [papers])

  const filterCategoryOptions = useMemo(() => {
    // Keep the shared taxonomy visible even when a newly created field has no
    // library papers yet. This makes category changes made in 识途/索骥
    // immediately discoverable from the library filter.
    const configured = [...categoryOptions]
    const configuredSet = new Set(categoryOptions)
    const historical = [...categoryCounts.keys()]
      .filter(category => !configuredSet.has(category))
      .sort((a, b) => a.localeCompare(b, 'zh-CN'))
    return [...configured, ...historical]
  }, [categoryCounts, categoryOptions])

  const filtered = useMemo(() => {
    let list = papers
    if (filter === 'processed') list = list.filter(p => p.processed)
    else if (filter === 'pending') list = list.filter(p => !p.processed && !p.error)
    else if (filter === 'failed') list = list.filter(p => p.error && !p.processed)
    if (categoryFilter !== CATEGORY_FILTER_ALL) {
      list = list.filter(p => effectivePaperCategoryLabel(p) === categoryFilter)
    }
    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter(p =>
        p.filename.toLowerCase().includes(q) ||
        (p.title || '').toLowerCase().includes(q) ||
        p.authors.some(a => a.toLowerCase().includes(q))
      )
    }
    return list
  }, [papers, filter, categoryFilter, search])

  return (
    <div className="flex h-full">
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {/* Toolbar */}
        <header className="bg-[var(--surface-0f1117)] border-b border-slate-800/80 px-6 py-4">
          <div className="flex flex-wrap items-start gap-4">
            <div className="min-w-0">
              <h1 className="text-xl font-semibold text-foreground tracking-tight">{tr("资料库")}</h1>
              <p className="text-sm text-slate-500 mt-1">
                {tr("浏览扫描到的论文，查看处理状态，并快速进入详情或重试失败项。")}</p>
              <p className="text-xs text-slate-500 mt-1.5">
                {tr("共")}{' '}{papers.length} {tr("篇")}<span className="text-emerald-400"> · {stats.processed} {tr("已处理")}</span>
                {stats.pending > 0 && <span className="text-slate-400"> · {stats.pending} {tr("待处理")}</span>}
                {stats.failed > 0 && <span className="text-red-400"> · {stats.failed} {tr("失败")}</span>}
              </p>
            </div>

            <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
              {actionNotice && (
                <TaskNotice
                  tone={actionNotice.tone}
                  title={actionNotice.title}
                  detail={actionNotice.detail}
                  className="max-w-md"
                  testId={actionNotice.autoDismissId != null ? 'paper-delete-success-notice' : undefined}
                />
              )}
              <div className="relative">
                <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
                <input
                  type="text"
                  placeholder={tr("搜索论文")}
                  value={search}
                  onChange={e => setSearch(e.target.value)}
                  className="bg-slate-900/60 border border-slate-700/60 rounded-xl text-sm text-slate-200 pl-9 pr-3 py-2 w-64 focus:outline-none focus:border-indigo-500/60 transition-colors placeholder:text-slate-500"
                />
              </div>
              <div className="flex items-center bg-slate-900/60 border border-slate-700/60 rounded-xl p-0.5">
                <button
                  onClick={() => setView('grid')}
                  className={`p-1.5 rounded-md transition-colors ${view === 'grid' ? 'bg-slate-800 text-slate-200' : 'text-slate-500 hover:text-slate-300'}`}
                  title={tr("网格视图")}
                >
                  <LayoutGrid size={14} />
                </button>
                <button
                  onClick={() => setView('list')}
                  className={`p-1.5 rounded-md transition-colors ${view === 'list' ? 'bg-slate-800 text-slate-200' : 'text-slate-500 hover:text-slate-300'}`}
                  title={tr("列表视图")}
                >
                  <ListIcon size={14} />
                </button>
              </div>
              <input
                ref={fileInputRef}
                type="file"
                accept="application/pdf,.pdf"
                multiple
                className="hidden"
                onChange={handleFilesSelected}
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading || pendingFiles.length > 0}
                className="inline-flex items-center gap-2 rounded-xl border border-indigo-500/30 bg-indigo-500/10 px-3 py-2 text-sm text-indigo-200 transition-colors hover:bg-indigo-500/20 disabled:cursor-not-allowed disabled:opacity-50"
                title={tr("从本地选择 PDF 拷贝到资料库 (./papers)")}
              >
                <Upload size={14} />
                {tr("上传 PDF")}</button>
              <button
                onClick={handleProcessPendingAll}
                disabled={bulkProcessingPending || !!status?.running || stats.pending === 0}
                className="inline-flex items-center gap-2 rounded-xl border border-sky-500/30 bg-sky-500/10 px-3 py-2 text-sm text-sky-200 transition-colors hover:bg-sky-500/20 disabled:cursor-not-allowed disabled:border-slate-800 disabled:bg-slate-900/50 disabled:text-slate-500"
                title={stats.pending > 0 ? tr("处理全部 {0} 篇待处理论文", { 0: stats.pending }) : tr("当前没有待处理论文")}
              >
                {bulkProcessingPending ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                {tr("重试全部待处理")}</button>
              <button
                onClick={handleRetryFailedAll}
                disabled={bulkRetrying || !!status?.running || stats.failed === 0}
                className="inline-flex items-center gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-200 transition-colors hover:bg-amber-500/20 disabled:cursor-not-allowed disabled:border-slate-800 disabled:bg-slate-900/50 disabled:text-slate-500"
                title={stats.failed > 0 ? tr("重试全部 {0} 篇失败论文", { 0: stats.failed }) : tr("当前没有失败论文")}
              >
                {bulkRetrying ? <Loader2 size={14} className="animate-spin" /> : <RotateCw size={14} />}
                {tr("重试全部失败")}</button>
              <button
                onClick={load}
                className="p-2 text-slate-400 hover:text-slate-200 hover:bg-slate-800/60 rounded-xl transition-colors"
                title={tr("刷新")}
              >
                <RefreshCw size={14} />
              </button>
            </div>
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-1.5 text-xs">
            {([
              ['all', tr("全部"), papers.length],
              ['processed', tr("已处理"), stats.processed],
              ['pending', tr("待处理"), stats.pending],
              ['failed', tr("失败"), stats.failed],
            ] as [Filter, string, number][]).map(([key, label, n]) => (
              <button
                key={key}
                onClick={() => setFilter(key)}
                className={`px-3 py-1.5 rounded-lg border transition-colors ${
                  filter === key
                    ? 'bg-indigo-500/20 text-indigo-300 border-indigo-500/20'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50 border-transparent'
                }`}
              >
                {label} <span className="text-slate-600 tabular-nums">{n}</span>
              </button>
            ))}

            <span className="mx-1.5 h-4 w-px bg-slate-800" aria-hidden="true" />
            <div className="relative">
              <label htmlFor="paper-category-filter" className="sr-only">{tr("按论文大类筛选")}</label>
              <span
                className={`pointer-events-none absolute left-2.5 top-1/2 h-1.5 w-1.5 -translate-y-1/2 rounded-full ${
                  categoryFilter === CATEGORY_FILTER_ALL
                    ? 'bg-slate-500'
                    : categoryDotColor(categoryFilter)
                }`}
                aria-hidden="true"
              />
              <select
                id="paper-category-filter"
                value={categoryFilter}
                onChange={event => setCategoryFilter(event.target.value)}
                className="h-8 w-40 cursor-pointer truncate rounded-lg border border-slate-700/70 bg-slate-900/60 py-1 pl-6 pr-7 text-xs font-medium text-slate-300 outline-none transition-colors hover:border-slate-600 focus:border-indigo-500/60"
              >
                <option value={CATEGORY_FILTER_ALL}>{tr("全部大类 ·")}{' '}{papers.length}</option>
                {filterCategoryOptions.map(category => (
                  <option key={category} value={category}>
                    {category} · {categoryCounts.get(category) || 0}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {recentFailures.length > 0 && (
            <div className="mt-3 rounded-xl border border-rose-500/25 bg-rose-500/10 px-3 py-2.5">
              <p className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium text-rose-200">
                <AlertTriangle size={12} />
                {tr("最近失败摘要")}</p>
              <ul className="space-y-1.5">
                {recentFailures.map(item => (
                  <li key={item.id} className="text-[11px] leading-relaxed text-rose-100/90 text-safe-wrap">
                    <span className="font-medium text-rose-100">{item.title}</span>
                    <span className="text-rose-200/80"> · {item.summary}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </header>

        {/* Selected-paper strip — sits between toolbar and the grid as a
            compact horizontal panel. Replaces the previous full-height
            right-column detail view; the right column is now dedicated
            to the Prompt editor. */}
        {selected && (
          <PaperDetailStrip
            paper={selected}
            pending={isPending(selected)}
            onClose={() => setSelected(null)}
            onProcess={() => handleProcessOne(selected)}
            onRetry={() => handleRetry(selected)}
            onReprocess={() => handleReprocess(selected)}
            onDelete={() => requestDeletePaper(selected)}
          />
        )}

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-6">
          {loading ? (
            <div className="text-slate-500 text-center py-24 text-sm">{tr("加载中…")}</div>
          ) : filtered.length === 0 ? (
            <div className="text-slate-500 text-center py-24">
              <FileText size={32} className="mx-auto text-slate-700 mb-3" />
              <p className="text-sm">{papers.length === 0 ? tr("还没有论文") : tr("没有匹配的论文")}</p>
              {papers.length === 0 && (
                <p className="text-xs mt-2 text-slate-600">
                  {tr("前往「图谱」页点击「扫描目录」")}</p>
              )}
            </div>
          ) : view === 'grid' ? (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-4">
              {filtered.map(p => (
                <PaperGridCard
                  key={p.id}
                  paper={p}
                  active={selected?.id === p.id}
                  pending={isPending(p)}
                  onClick={() => handlePaperClick(p)}
                  onProcessAction={() => {
                    const action = paperCardProcessAction(p)
                    if (action?.kind === 'retry') void handleRetry(p)
                    else void handleProcessOne(p)
                  }}
                  onDelete={() => requestDeletePaper(p)}
                  categoryOptions={categoryOptions}
                  categoryValue={categoryDrafts[p.id] || p.paper_category_override || CATEGORY_INHERIT}
                  categorySaving={categorySavingIds.has(p.id)}
                  onCategoryChange={value => handleCategoryChange(p, value)}
                />
              ))}
            </div>
          ) : (
            <div className="max-w-4xl space-y-2">
              {filtered.map(p => (
                <PaperListRow
                  key={p.id}
                  paper={p}
                  active={selected?.id === p.id}
                  pending={isPending(p)}
                  onClick={() => handlePaperClick(p)}
                  onProcessAction={() => {
                    const action = paperCardProcessAction(p)
                    if (action?.kind === 'retry') void handleRetry(p)
                    else void handleProcessOne(p)
                  }}
                  onDelete={() => requestDeletePaper(p)}
                  categoryOptions={categoryOptions}
                  categoryValue={categoryDrafts[p.id] || p.paper_category_override || CATEGORY_INHERIT}
                  categorySaving={categorySavingIds.has(p.id)}
                  onCategoryChange={value => handleCategoryChange(p, value)}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Right column: global extraction Prompt editor. Collapses to a
          12-wide icon rail by default so the paper grid claims the full
          horizontal width; click the chevron to expand back to ~24rem.
          The Prompt rarely changes between visits, so keeping it
          hidden-but-discoverable matches actual usage frequency. */}
      {promptOpen ? (
        <aside className="w-[24rem] max-w-[38vw] bg-[var(--surface-0f1117)] border-l border-slate-800/80 flex flex-col overflow-hidden shrink-0 transition-[width] duration-200 ease-out">
          <div className="px-5 py-4 border-b border-slate-800/80 flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <p className="section-label mb-1">{tr("全局 Prompt")}</p>
              <p className="text-sm text-slate-500 leading-relaxed">
                {tr("论文抽取使用的指令，所有论文共享。")}</p>
            </div>
            <button
              onClick={() => setPromptOpen(false)}
              title={tr("收起到右侧图标条")}
              className="shrink-0 p-1.5 rounded-md text-slate-500 hover:text-slate-200 hover:bg-slate-800/60"
            >
              <PanelRightClose size={14} />
            </button>
          </div>
          <PromptPanel />
        </aside>
      ) : (
        <aside className="w-12 bg-[var(--surface-0f1117)] border-l border-slate-800/80 flex flex-col items-center py-4 gap-3 shrink-0 transition-[width] duration-200 ease-out">
          <button
            onClick={() => setPromptOpen(true)}
            title={tr("展开全局 Prompt 编辑器")}
            className="p-1.5 text-slate-400 hover:text-foreground rounded-md hover:bg-slate-800/60"
          >
            <PanelRightOpen size={16} />
          </button>
          <div className="w-full border-t border-slate-800/80" />
          <button
            onClick={() => setPromptOpen(true)}
            title={tr("全局 Prompt")}
            className="p-1.5 text-slate-500 hover:text-slate-200 rounded-md hover:bg-slate-800/60"
          >
            <Pencil size={14} />
          </button>
        </aside>
      )}

      {/* Upload confirm — review the picked PDFs before copying to ./papers. */}
      {deleteTarget && (
          <PaperDeleteDialog
            paper={deleteTarget}
            busy={deleteBusy}
            error={deleteError}
            onCancel={cancelDeletePaper}
            onConfirm={() => void confirmDeletePaper()}
          />
        )}
        {pendingFiles.length > 0 && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-4 backdrop-blur-sm"
          onClick={e => { if (e.target === e.currentTarget && !uploading) setPendingFiles([]) }}
        >
          <div className="w-full max-w-lg rounded-2xl border border-slate-800 bg-[var(--surface-0f1117)] shadow-2xl">
            <div className="flex items-center gap-2 border-b border-slate-800 px-5 py-3">
              <Upload size={15} className="text-indigo-300" />
              <h3 className="text-sm font-semibold text-foreground">{tr("确认上传")}{' '}{pendingFiles.length} {tr("个 PDF")}</h3>
              <button
                onClick={() => !uploading && setPendingFiles([])}
                className="ml-auto rounded p-1 text-slate-500 hover:bg-slate-800/60 hover:text-slate-200"
              >
                <XCircle size={15} />
              </button>
            </div>
            <div className="max-h-72 space-y-0.5 overflow-y-auto px-5 py-3">
              {pendingFiles.map((f, i) => (
                <div
                  key={i}
                  className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-[12px] text-slate-300 hover:bg-slate-800/40"
                >
                  <FileText size={13} className="shrink-0 text-indigo-300/70" />
                  <span className="min-w-0 flex-1 truncate">{f.name}</span>
                  <span className="shrink-0 tabular-nums text-[10.5px] text-slate-500">
                    {fmtSize(f.size)}
                  </span>
                </div>
              ))}
            </div>
            <div className="space-y-2.5 border-t border-slate-800 px-5 py-3.5">
              <p className="text-[11px] leading-relaxed text-slate-500">
                {tr("将拷贝到")}<code className="rounded bg-slate-800/70 px-1 text-slate-400">./papers</code>
                {tr("，已存在或重复的会自动跳过。")}</p>
              <div className="flex items-center justify-end gap-2">
                <button
                  onClick={() => setPendingFiles([])}
                  disabled={uploading}
                  className="rounded-lg border border-slate-700 px-4 py-1.5 text-[12px] text-slate-300 transition-colors hover:bg-slate-800 disabled:opacity-50"
                >
                  {tr("取消")}</button>
                <button
                  onClick={handleConfirmUpload}
                  disabled={uploading}
                  className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-indigo-500 px-4 py-1.5 text-[12px] font-medium text-white transition-colors hover:bg-indigo-400 disabled:opacity-50"
                >
                  {uploading ? <Loader2 size={13} className="animate-spin" /> : <Upload size={13} />}
                  {uploading ? tr("上传中…") : tr("确认上传")}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// Compact horizontal strip shown between the toolbar and the paper grid
// when a paper is selected. Replaces the full-height right-column detail
// view; the right column is now Prompt-only.
function PaperDetailStrip({
  paper, pending, onClose, onProcess, onRetry, onReprocess, onDelete,
}: {
  paper: PaperRecord
  pending: boolean
  onClose: () => void
  onProcess: () => void
  onRetry: () => void
  onReprocess: () => void
  onDelete: () => void
}) {
  useLocale()
  const stage = inferPaperProcessMeta(paper, null, pending)
  return (
    <div className="bg-[var(--surface-0f1117)] border-b border-slate-800/80 px-6 py-3">
      <div className="flex items-start gap-4">
        {/* Thumbnail */}
        <div className="w-14 h-[4.5rem] shrink-0 bg-[var(--surface-0b0d12)] rounded border border-slate-800 flex items-center justify-center overflow-hidden">
          <PaperCover key={paper.id} paper={paper} iconSize={18} />
        </div>

        {/* Info */}
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <p className="text-sm text-foreground font-semibold leading-snug line-clamp-2 text-safe-wrap">
              {paper.title || paper.filename}
            </p>
            <button
              onClick={onClose}
              title={tr("收起")}
              className="shrink-0 text-slate-500 hover:text-foreground text-sm rounded-md px-1.5 py-0.5 hover:bg-slate-800/60 transition-colors"
            >
              ✕
            </button>
          </div>

          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500">
            <PaperProcessBadge paper={paper} pending={pending} />
            {paper.authors.length > 0 && (
              <span className="line-clamp-1 max-w-[28rem] text-safe-wrap">
                {paper.authors.slice(0, 4).join(', ')}
                {paper.authors.length > 4 ? ' …' : ''}
              </span>
            )}
            {paper.num_pages && (
              <span className="tabular-nums text-slate-400">{paper.num_pages} {tr("页")}</span>
            )}
            {paper.processed_at && (
              <span title={paper.processed_at}>
                {tr("于")}{' '}{new Date(paper.processed_at).toLocaleString(getFormattingLocale())} {tr("处理")}</span>
            )}
            <span className="font-mono text-slate-600 break-all">{paper.filename}</span>
          </div>

          <div className="mt-2 rounded-lg border border-slate-800/70 bg-slate-900/50 px-2.5 py-1.5 text-[11px] text-slate-300">
            <span className="font-semibold text-slate-200">{tr("阶段：")}</span>
            <span className="ml-1">{stage.summary}</span>
          </div>

          {stage.errorSummary && (
            <div className="mt-2 bg-red-500/10 border border-red-500/30 rounded-md px-2.5 py-1.5 text-[11px] text-red-300 break-words leading-relaxed">
              <span className="font-semibold">{tr("最近错误：")}</span>{stage.errorSummary}
            </div>
          )}

          {/* Actions */}
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            {!paper.processed && !paper.error && (
              <button
                onClick={onProcess}
                disabled={pending}
                className="inline-flex items-center gap-1 text-xs bg-indigo-500 hover:bg-indigo-400 text-white px-2.5 py-1 rounded-md transition-colors disabled:bg-slate-700 disabled:text-slate-400"
              >
                {pending ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />} {tr("立即处理")}</button>
            )}
            {paper.error && (
              <button
                onClick={onRetry}
                disabled={pending}
                className="inline-flex items-center gap-1 text-xs bg-amber-500 hover:bg-amber-400 text-white px-2.5 py-1 rounded-md transition-colors disabled:bg-slate-700 disabled:text-slate-400"
              >
                {pending ? <Loader2 size={11} className="animate-spin" /> : <RotateCw size={11} />} {tr("重试处理")}</button>
            )}
            {paper.processed && (
              <button
                onClick={onReprocess}
                disabled={pending}
                className="inline-flex items-center gap-1 text-xs bg-slate-800/80 hover:bg-slate-700 text-slate-200 border border-slate-700/60 px-2.5 py-1 rounded-md transition-colors disabled:opacity-40"
              >
                {pending ? <Loader2 size={11} className="animate-spin" /> : <RotateCw size={11} />} {tr("重新处理")}</button>
            )}
            <button
              type="button"
              data-testid="paper-detail-delete"
              onClick={event => {
                event.preventDefault()
                event.stopPropagation()
                onDelete()
              }}
              className="inline-flex items-center gap-1 text-xs text-rose-300 bg-rose-500/10 hover:bg-rose-500/20 border border-rose-500/30 px-2.5 py-1 rounded-md transition-colors"
            >
              <Trash2 size={11} /> {tr("删除")}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function PaperCover({ paper, iconSize }: { paper: PaperRecord; iconSize: number }) {
  const [failed, setFailed] = useState(false)
  if (failed) {
    return <FileText size={iconSize} className="text-slate-700" data-testid="paper-cover-fallback" />
  }
  return (
    <img
      src={firstPageUrl(paper.id)}
      alt={paper.filename}
      data-testid="paper-cover-image"
      className="max-w-full max-h-full object-contain"
      onError={() => setFailed(true)}
    />
  )
}

function PaperProcessButton({
  paper, pending, onProcessAction,
}: {
  paper: PaperRecord
  pending: boolean
  onProcessAction: () => void
}) {
  const action = paperCardProcessAction(paper)
  if (!action) return null
  return (
    <button
      type="button"
      data-testid="paper-card-process"
      aria-label={action.label}
      disabled={pending}
      aria-busy={pending}
      onClick={event => {
        event.preventDefault()
        event.stopPropagation()
        onProcessAction()
      }}
      onKeyDown={event => event.stopPropagation()}
      className="inline-flex shrink-0 items-center gap-1 rounded-md bg-indigo-500 px-2.5 py-1 text-[11px] font-medium text-white transition-colors hover:bg-indigo-400 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
    >
      {pending ? <Loader2 size={11} className="animate-spin" /> : <Play size={11} />}
      {action.label}
    </button>
  )
}

function paperDisplayName(paper: PaperRecord): string {
  return paper.title || paper.filename
}

function PaperTitle({ paper, lines }: { paper: PaperRecord; lines: 2 | 3 }) {
  const name = paperDisplayName(paper)
  return (
    <p
      data-testid="paper-card-title"
      title={name}
      aria-label={name}
      className={`min-w-0 max-w-full overflow-hidden text-sm font-medium leading-snug text-slate-200 text-safe-wrap group-hover:text-foreground transition-colors ${
        lines === 3 ? 'line-clamp-3 min-h-[calc(1.375em*3)]' : 'line-clamp-2 min-h-[calc(1.375em*2)]'
      }`}
    >
      {name}
    </p>
  )
}

function PaperGridCard({
  paper, active, pending, onClick, onProcessAction, onDelete, categoryOptions, categoryValue, categorySaving, onCategoryChange,
}: {
  paper: PaperRecord
  active: boolean
  pending: boolean
  onClick: () => void
  onProcessAction: () => void
  onDelete: () => void
  categoryOptions: string[]
  categoryValue: string
  categorySaving: boolean
  onCategoryChange: (value: string) => void
}) {
  useLocale()
  return (
    <article
      role="button"
      tabIndex={0}
      data-testid="paper-grid-card"
      onClick={onClick}
      onKeyDown={event => {
        if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault()
          onClick()
        }
      }}
      className={`text-left group bg-slate-900/40 rounded-2xl border transition-all cursor-pointer focus:outline-none focus:ring-2 focus:ring-indigo-500/50 ${
        active
          ? 'border-indigo-500/60 shadow-lg shadow-indigo-500/10'
          : 'border-slate-800 hover:border-slate-700 hover:bg-slate-900/70'
      }`}
    >
      <div className="relative aspect-[3/4] overflow-hidden rounded-t-2xl bg-[var(--surface-0b0d12)] flex items-center justify-center">
        <PaperCover key={paper.id} paper={paper} iconSize={40} />
        <div className="absolute top-2 left-2 z-10">
          <PaperActionsMenu paper={paper} onDelete={onDelete} />
        </div>
        <div className="absolute top-2 right-2">
          <StatusDot pending={pending} paper={paper} />
        </div>
      </div>
      <div className="min-w-0 overflow-hidden p-3.5">
        <PaperTitle paper={paper} lines={3} />
        {paper.authors.length > 0 && (
          <p className="mt-1.5 min-h-[2.5rem] text-xs leading-relaxed text-slate-500 line-clamp-2 text-safe-wrap">
            {paper.authors.slice(0, 3).join(', ')}
          </p>
        )}
        <div className="mt-2.5 flex min-w-0 items-center justify-between gap-2 text-xs text-slate-600">
          <span className="tabular-nums shrink-0">{paper.num_pages ? tr("{0} 页", { 0: paper.num_pages }) : ''}</span>
          <PaperProcessButton paper={paper} pending={pending} onProcessAction={onProcessAction} />
        </div>
        <div className="mt-1.5 min-w-0 w-full max-w-full">
          <InlinePaperCategoryEditor
            paper={paper}
            options={categoryOptions}
            value={categoryValue}
            saving={categorySaving}
            onChange={onCategoryChange}
            variant="grid"
          />
        </div>
      </div>
    </article>
  )
}

function PaperListRow({
  paper, active, pending, onClick, onProcessAction, onDelete, categoryOptions, categoryValue, categorySaving, onCategoryChange,
}: {
  paper: PaperRecord
  active: boolean
  pending: boolean
  onClick: () => void
  onProcessAction: () => void
  onDelete: () => void
  categoryOptions: string[]
  categoryValue: string
  categorySaving: boolean
  onCategoryChange: (value: string) => void
}) {
  useLocale()
  const stage = inferPaperProcessMeta(paper, null, pending)
  return (
    <article
      role="button"
      tabIndex={0}
      data-testid="paper-list-row"
      onClick={onClick}
      onKeyDown={event => {
        if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault()
          onClick()
        }
      }}
      className={`w-full min-w-0 flex items-start gap-4 px-4 py-3.5 rounded-xl border text-left transition-all cursor-pointer focus:outline-none focus:ring-2 focus:ring-indigo-500/50 ${
        active
          ? 'bg-indigo-500/5 border-indigo-500/40'
          : 'bg-slate-900/40 border-slate-800 hover:border-slate-700 hover:bg-slate-900/70'
      }`}
    >
      <div className="w-10 h-12 shrink-0 bg-[var(--surface-0b0d12)] rounded flex items-center justify-center overflow-hidden">
        <PaperCover key={paper.id} paper={paper} iconSize={16} />
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-2 sm:flex-row sm:items-start">
        <div className="min-w-0 flex-1">
          <PaperTitle paper={paper} lines={2} />
          <p className="mt-1 text-xs leading-relaxed text-slate-500 line-clamp-2 text-safe-wrap">
            {paper.authors.length > 0 ? paper.authors.slice(0, 3).join(', ') : paper.filename}
            {paper.num_pages && <span className="text-slate-600"> · {paper.num_pages}p</span>}
          </p>
          <p className={`mt-1 text-[11px] leading-relaxed text-safe-wrap ${stage.stage === 'failed' ? 'text-rose-300' : 'text-slate-500'}`}>
            {stage.stage === 'failed' ? (stage.errorSummary || stage.summary) : stage.summary}
          </p>
        </div>
        <div className="flex min-w-0 w-full flex-col items-stretch gap-2 sm:max-w-[16rem]">
          <InlinePaperCategoryEditor
            paper={paper}
            options={categoryOptions}
            value={categoryValue}
            saving={categorySaving}
            onChange={onCategoryChange}
            variant="list"
          />
          <div className="flex flex-wrap items-center gap-2">
            <PaperProcessButton paper={paper} pending={pending} onProcessAction={onProcessAction} />
            <PaperProcessBadge paper={paper} pending={pending} />
            <PaperActionsMenu paper={paper} onDelete={onDelete} />
          </div>
        </div>
      </div>
    </article>
  )
}


function paperActionName(paper: PaperRecord): string {
  return paper.title || paper.filename
}

function PaperActionsMenu({ paper, onDelete }: { paper: PaperRecord; onDelete: () => void }) {
  useLocale()
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const dismiss = useCallback(() => setOpen(false), [])
  useDismissOnOutside(open, dismiss, rootRef)
  return (
    <div
      ref={rootRef}
      className="relative"
      data-testid="paper-card-actions"
      onClick={event => event.stopPropagation()}
      onKeyDown={event => event.stopPropagation()}
    >
      <button
        type="button"
        data-testid="paper-card-actions-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={tr("{0}的操作", { 0: paperActionName(paper) })}
        title={tr("操作")}
        onClick={event => {
          event.preventDefault()
          event.stopPropagation()
          setOpen(current => !current)
        }}
        className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-slate-950/70 text-slate-300 shadow-sm hover:bg-slate-800 hover:text-white focus:outline-none focus:ring-1 focus:ring-slate-500"
      >
        <MoreVertical size={14} />
      </button>
      {open && (
        <div
          role="menu"
          data-testid="paper-card-actions-menu"
          className="absolute left-0 z-30 mt-1 min-w-[9rem] rounded-md border border-slate-700 bg-slate-900 py-1 text-[11px] leading-snug text-slate-200 shadow-lg shadow-black/40"
        >
          <button
            type="button"
            role="menuitem"
            data-testid="paper-card-delete"
            onClick={event => {
              event.preventDefault()
              event.stopPropagation()
              setOpen(false)
              onDelete()
            }}
            className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-left text-rose-300 hover:bg-rose-500/10"
          >
            <Trash2 size={11} /> {tr("删除")}
          </button>
        </div>
      )}
    </div>
  )
}

function PaperDeleteDialog({
  paper, busy, error, onCancel, onConfirm,
}: {
  paper: PaperRecord
  busy: boolean
  error: string | null
  onCancel: () => void
  onConfirm: () => void
}) {
  useLocale()
  const name = paperActionName(paper)
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 px-4"
      data-testid="paper-delete-dialog-backdrop"
      onClick={event => {
        if (event.target === event.currentTarget && !busy) onCancel()
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="paper-delete-title"
        data-testid="paper-delete-dialog"
        className="w-full max-w-md rounded-2xl border border-slate-700 bg-slate-900 p-5 shadow-2xl shadow-black/50"
        onClick={event => event.stopPropagation()}
      >
        <h2 id="paper-delete-title" className="text-base font-semibold text-slate-100">
          {tr("永久删除这篇论文？")}
        </h2>
        <p className="mt-2 text-sm font-medium leading-relaxed text-slate-200 text-safe-wrap">
          {name}
        </p>
        <p className="mt-2 text-sm leading-relaxed text-slate-400">
          {tr("将永久删除该论文记录、Knowra 管理目录内的原始 PDF、缩略图、抽取结果、论文 Wiki、检索索引和引用关系。此操作不可恢复。")}
        </p>
        <p className="mt-2 text-xs leading-relaxed text-slate-500">
          {tr("其他论文仍在使用的概念会保留，并在下次概念编译时不再使用这篇已删除论文。位于管理目录外的导入源默认不会删除。")}
        </p>
        {error && (
          <p data-testid="paper-delete-error" className="mt-3 rounded-md border border-rose-500/30 bg-rose-500/10 px-2.5 py-1.5 text-xs text-rose-300">
            {error}
          </p>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            data-testid="paper-delete-cancel"
            disabled={busy}
            onClick={onCancel}
            className="rounded-md px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-40"
          >
            {tr("取消")}
          </button>
          <button
            type="button"
            data-testid="paper-delete-confirm"
            disabled={busy}
            onClick={onConfirm}
            className="inline-flex items-center gap-1 rounded-md bg-rose-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-rose-500 disabled:cursor-wait disabled:bg-rose-900 disabled:text-rose-200"
          >
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
            {tr("永久删除")}
          </button>
        </div>
      </div>
    </div>
  )
}

function inheritedCategoryOptionLabel(paper: PaperRecord): string {
  return `${tr("跟随 ·")} ${paper.paper_category_model || tr("未设置")}`
}

function visibleCategoryLabel(paper: PaperRecord, value: string): string {
  return value === CATEGORY_INHERIT ? inheritedCategoryOptionLabel(paper) : value
}

function categorySourceHint(value: string): string {
  return value === CATEGORY_INHERIT ? tr("跟随模型") : tr("人工")
}

function InlinePaperCategoryEditor({
  paper, options, value, saving, onChange, variant,
}: {
  paper: PaperRecord
  options: string[]
  value: string
  saving: boolean
  onChange: (value: string) => void
  variant: 'grid' | 'list'
}) {
  useLocale()
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLUListElement>(null)
  const focusOnOpenRef = useRef(0)
  const dismiss = useCallback(() => setOpen(false), [])
  useDismissOnOutside(open, dismiss, rootRef, menuRef)
  const { style: menuStyle, placement } = useAnchoredMenu(open, triggerRef, menuRef, dismiss)
  const choices = [CATEGORY_INHERIT, ...options]
  const selectedIndex = Math.max(0, choices.indexOf(value))
  const [activeIndex, setActiveIndex] = useState(selectedIndex)
  const label = visibleCategoryLabel(paper, value)
  const sourceHint = categorySourceHint(value)
  const triggerId = `paper-category-${variant}-${paper.id}`
  const menuId = `${triggerId}-menu`
  const effectiveSwatch = value === CATEGORY_INHERIT
    ? (paper.paper_category_model || paper.paper_category || tr("未设置"))
    : value
  const optionLabel = (choice: string) => (
    choice === CATEGORY_INHERIT ? inheritedCategoryOptionLabel(paper) : choice
  )
  const openMenu = () => {
    focusOnOpenRef.current = selectedIndex
    setActiveIndex(selectedIndex)
    setOpen(true)
  }
  const choose = (next: string) => {
    setOpen(false)
    triggerRef.current?.focus()
    onChange(next)
  }
  const moveActive = (next: number) => {
    const index = (next + choices.length) % choices.length
    setActiveIndex(index)
    const option = menuRef.current?.querySelectorAll<HTMLElement>('[role="option"]')[index]
    option?.scrollIntoView({ block: 'nearest' })
    option?.focus()
  }
  const onMenuKeyDown = (event: KeyboardEvent) => {
    event.stopPropagation()
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      moveActive(activeIndex + 1)
    } else if (event.key === 'ArrowUp') {
      event.preventDefault()
      moveActive(activeIndex - 1)
    } else if (event.key === 'Home') {
      event.preventDefault()
      moveActive(0)
    } else if (event.key === 'End') {
      event.preventDefault()
      moveActive(choices.length - 1)
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      choose(choices[activeIndex] ?? CATEGORY_INHERIT)
    } else if (event.key === 'Escape') {
      event.preventDefault()
      setOpen(false)
      triggerRef.current?.focus()
    } else if (event.key === 'Tab') {
      setOpen(false)
    }
  }
  useEffect(() => {
    if (!open) return
    const frame = window.requestAnimationFrame(() => {
      const option = menuRef.current?.querySelectorAll<HTMLElement>('[role="option"]')[focusOnOpenRef.current]
      ;(option ?? menuRef.current)?.focus()
    })
    return () => window.cancelAnimationFrame(frame)
  }, [open])
  return (
    <div
      ref={rootRef}
      className="paper-card-category-control relative w-full min-w-0 max-w-full"
      data-testid="paper-card-category"
      onClick={event => event.stopPropagation()}
      onKeyDown={event => event.stopPropagation()}
    >
      <button
        type="button"
        ref={triggerRef}
        id={triggerId}
        data-testid="paper-card-category-label"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={menuId}
        aria-label={tr("{0}的论文大类", { 0: paperDisplayName(paper) })}
        title={`${label}（${sourceHint}）`}
        disabled={saving}
        onClick={event => {
          event.preventDefault()
          event.stopPropagation()
          if (open) setOpen(false)
          else openMenu()
        }}
        onKeyDown={event => {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault()
            if (!open) openMenu()
          }
        }}
        className="flex w-full min-w-0 max-w-full items-center gap-1.5 rounded-md px-1 py-0.5 text-left text-[11px] leading-snug text-slate-400 hover:bg-slate-800/60 hover:text-slate-200 focus:outline-none focus:ring-1 focus:ring-slate-500 disabled:cursor-wait disabled:opacity-60"
      >
        <span
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${categoryDotColor(effectiveSwatch)}`}
          aria-hidden="true"
        />
        <span className="min-w-0 flex-1 line-clamp-2 break-words whitespace-normal">
          {label}
        </span>
        {saving ? (
          <Loader2 size={10} className="shrink-0 animate-spin text-current" aria-label={tr("保存中")} />
        ) : (
          <ChevronDown size={10} className={`shrink-0 text-slate-500 transition-transform ${open ? 'rotate-180' : ''}`} aria-hidden="true" />
        )}
      </button>
      {open && createPortal(
        <ul
          ref={menuRef}
          id={menuId}
          role="listbox"
          tabIndex={-1}
          data-testid="paper-card-category-menu"
          data-placement={placement}
          aria-labelledby={triggerId}
          aria-activedescendant={`${menuId}-option-${activeIndex}`}
          style={menuStyle}
          onKeyDown={onMenuKeyDown}
          onClick={event => event.stopPropagation()}
          className="paper-card-category-menu rounded-md border border-slate-700 bg-slate-900 py-1 text-[11px] leading-snug text-slate-200 shadow-lg shadow-black/40"
        >
          {choices.map((choice, index) => (
            <li key={choice} role="presentation">
              <button
                type="button"
                id={`${menuId}-option-${index}`}
                role="option"
                tabIndex={index === activeIndex ? 0 : -1}
                aria-selected={value === choice}
                title={optionLabel(choice)}
                onMouseDown={event => event.preventDefault()}
                onMouseEnter={() => setActiveIndex(index)}
                onClick={event => {
                  event.preventDefault()
                  event.stopPropagation()
                  choose(choice)
                }}
                className={`flex w-full px-2 py-1 text-left hover:bg-slate-800 ${
                  value === choice ? 'bg-slate-800/80 text-slate-50' : ''
                } ${index === activeIndex ? 'ring-1 ring-inset ring-slate-500/70' : ''}`}
              >
                {optionLabel(choice)}
              </button>
            </li>
          ))}
        </ul>,
        document.body,
      )}
    </div>
  )
}

function categoryDotColor(category: string): string {
  if (!category || category === '未设置' || category === '其他') {
    return 'bg-slate-500'
  }
  if (CATEGORY_DOT_BY_NAME[category]) return CATEGORY_DOT_BY_NAME[category]
  let hash = 0
  for (const char of category) hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0
  return CATEGORY_DOT_COLORS[Math.abs(hash) % CATEGORY_DOT_COLORS.length]
}

function effectivePaperCategoryLabel(paper: PaperRecord): string {
  return paper.paper_category_override
    || paper.paper_category
    || paper.paper_category_model
    || tr("未设置")
}

function StatusDot({ paper, pending }: { paper: PaperRecord; pending: boolean }) {
  useLocale()
  if (pending) return <Loader2 size={16} className="text-indigo-400 animate-spin drop-shadow" />
  if (paper.processed) return <CheckCircle2 size={16} className="text-emerald-400 drop-shadow" />
  if (paper.error) return <XCircle size={16} className="text-red-400 drop-shadow" />
  return <Clock size={16} className="text-slate-500 drop-shadow" />
}

function fmtSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${Math.max(1, Math.round(bytes / 1024))} KB`
}

function getErrorMessage(error: unknown): string {
  const apiError = error as {
    response?: { data?: { detail?: string | { message?: string } } }
    message?: string
    code?: string
  }
  if (apiError.code === 'ECONNABORTED') return tr("请求超时，后端没有在 30 秒内响应。")
  const detail = apiError.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail === 'object' && typeof detail.message === 'string') return detail.message
  return apiError.message || tr("未知错误")
}
