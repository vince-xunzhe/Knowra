import { t as tr } from '../i18n/catalog'
import { useLocale } from '../i18n/preferences'
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Tags, Plus, Trash2, Pencil, Check, X, Loader2, Save, RotateCw, ArrowRight,
  ChevronRight, ChevronDown,
} from 'lucide-react'
import {
  listPaperCategories, addPaperCategory, renamePaperCategory, deletePaperCategory,
  previewPaperCategoryDeletion, retryPaperCategoryCleanup, bulkSetPaperCategory, listPapers,
  type PaperCategoryItem, type PaperRecord, type CategoryDeletionPreview,
} from '../api/client'

// Sentinel for "follow the model" (clears the manual override).
const INHERIT = '__inherit__'

function apiErr(e: unknown): string {
  const x = e as { response?: { data?: { detail?: unknown } }; message?: string }
  const detail = x?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (detail && typeof detail === 'object' && 'message' in detail && typeof (detail as { message?: unknown }).message === 'string') {
    return (detail as { message: string }).message
  }
  return x?.message || String(e)
}

function previewFromError(e: unknown): CategoryDeletionPreview | null {
  const detail = (e as { response?: { data?: { detail?: { preview?: CategoryDeletionPreview; research?: CategoryDeletionPreview['research'] } } } })?.response?.data?.detail
  if (detail && typeof detail === 'object' && detail.preview?.research) return detail.preview
  return null
}

/**
 * 编排大类 — taxonomy + assignment editor surfaced from the 编译图谱 view.
 *
 * Left: the categories (add / rename / delete).
 * Right: a BOARD — one lane per category, papers grouped inside. Select one or
 * more paper cards and move them to another lane. Changes stage locally and
 * commit together on 保存.
 */
export default function CategoryComposer({ onClose }: { onClose: () => void }) {
  useLocale()
  const [cats, setCats] = useState<PaperCategoryItem[]>([])
  const [papers, setPapers] = useState<PaperRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  // Staged override changes: paperId → category name, or INHERIT (follow model).
  const [pending, setPending] = useState<Map<string, string>>(new Map())
  // Selected paper cards (ids) awaiting a move.
  const [selected, setSelected] = useState<Set<string>>(new Set())
  // Which category groups are expanded (default: all collapsed → compact).
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  // Category-management local UI state.
  const [newName, setNewName] = useState('')
  const [editing, setEditing] = useState<string | null>(null)
  const [editName, setEditName] = useState('')
  const [renameConfirm, setRenameConfirm] = useState<{ from: string; to: string } | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState<{ name: string; count: number } | null>(null)
  const [deleteTarget, setDeleteTarget] = useState('')
  const [deletePreview, setDeletePreview] = useState<CategoryDeletionPreview | null>(null)
  const [confirmResearch, setConfirmResearch] = useState(false)
  const [confirmCanvases, setConfirmCanvases] = useState(false)
  const [cleanupWarning, setCleanupWarning] = useState<string | null>(null)
  const [pendingCleanupId, setPendingCleanupId] = useState<string | null>(null)

  const reload = useCallback(async () => {
    const [c, p] = await Promise.all([listPaperCategories(), listPapers()])
    setCats(c)
    setPapers(p)
  }, [])

  useEffect(() => {
    let cancelled = false
    Promise.all([listPaperCategories(), listPapers()])
      .then(([categories, records]) => {
        if (cancelled) return
        setCats(categories)
        setPapers(records)
      })
      .catch(e => { if (!cancelled) setError(apiErr(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  const catNames = useMemo(() => cats.map(c => c.name), [cats])
  const normalizedKey = (value: string) => value.trim().toLowerCase().replace(/[\s_/-]+/g, '')
  const conflictName = (value: string, exclude?: string) => {
    const cleaned = value.trim()
    if (!cleaned) return tr("分类名不能为空")
    const key = normalizedKey(cleaned)
    const found = cats.find(item => item.name !== exclude && (item.name === cleaned || normalizedKey(item.name) === key))
    return found ? tr("分类已存在：{0}", { 0: found.name }) : null
  }

  // The displayed (possibly-staged) category for a paper.
  const shownCategory = useCallback(
    (p: PaperRecord): string => {
      const staged = pending.get(String(p.id))
      if (staged !== undefined) {
        if (staged === INHERIT) return p.paper_category_model || '其他'
        return staged
      }
      return p.paper_category || '其他'
    },
    [pending],
  )

  // Group papers into lanes by their (staged) category.
  const grouped = useMemo(() => {
    const m = new Map<string, PaperRecord[]>()
    for (const c of catNames) m.set(c, [])
    for (const p of papers) {
      const c = shownCategory(p)
      if (!m.has(c)) m.set(c, [])
      m.get(c)!.push(p)
    }
    // Within a lane, order chronologically by publication year (oldest →
    // newest; unknown last), matching the 回顾 / 资料 / 泳道图 ordering.
    for (const [, ps] of m) {
      ps.sort((a, b) => {
        const ya = a.year || 9999
        const yb = b.year || 9999
        if (ya !== yb) return ya - yb
        return String(a.processed_at || '').localeCompare(String(b.processed_at || ''))
      })
    }
    return m
  }, [papers, catNames, shownCategory])

  const toggleSelect = (id: string) =>
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  // Move all currently-selected papers into `target` (a category or INHERIT).
  const moveSelectedTo = (target: string) => {
    setPending(prev => {
      const next = new Map(prev)
      for (const pid of selected) {
        const p = papers.find(x => String(x.id) === pid)
        if (!p) continue
        const current = p.paper_category_override || INHERIT
        if (target === current) next.delete(pid)
        else next.set(pid, target)
      }
      return next
    })
    setSelected(new Set())
  }

  const runManage = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key)
    setError(null)
    try {
      await fn()
      await reload()
    } catch (e) {
      setError(apiErr(e))
    } finally {
      setBusy(null)
    }
  }

  const handleSave = async () => {
    if (pending.size === 0) return
    setSaving(true)
    setError(null)
    try {
      const groups = new Map<string, string[]>()
      for (const [pid, val] of pending) {
        const arr = groups.get(val) || []
        arr.push(pid)
        groups.set(val, arr)
      }
      for (const [val, ids] of groups) {
        await bulkSetPaperCategory(ids, val === INHERIT ? null : val)
      }
      setPending(new Map())
      await reload()
    } catch (e) {
      setError(apiErr(e))
    } finally {
      setSaving(false)
    }
  }

  const toggleExpand = (name: string) =>
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  const allExpanded = catNames.length > 0 && catNames.every(n => expanded.has(n))

  // Clicking a category on the left expands it + scrolls it into view.
  const scrollToLane = (name: string) => {
    setExpanded(prev => new Set(prev).add(name))
    setTimeout(
      () =>
        document.getElementById(`lane-${name}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }),
      0,
    )
  }

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col bg-[var(--surface-0b0d12)]"
      data-testid="paper-category-editor-panel"
      role="dialog"
      aria-modal="true"
      aria-labelledby="paper-category-editor-title"
    >
      {/* header */}
      <header className="flex items-center gap-3 border-b border-slate-800 bg-[var(--surface-0f1117)] px-5 py-3">
        <Tags size={15} className="text-indigo-300" />
        <h2 id="paper-category-editor-title" className="text-sm font-semibold text-foreground">{tr("大类编辑")}</h2>
        <span className="text-[11px] text-slate-500">
          {tr("编辑的是论文大类，不是节点类型。左侧管理大类；右侧调整论文归属不会删除论文。")}</span>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => setPending(new Map())}
            disabled={pending.size === 0 || saving}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-1.5 text-[12px] text-slate-300 hover:bg-slate-800 disabled:opacity-40"
          >
            <RotateCw size={12} />
            {tr("撤销")}</button>
          <button
            onClick={handleSave}
            disabled={pending.size === 0 || saving}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-[12px] font-medium text-white hover:bg-indigo-400 disabled:opacity-40"
          >
            {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
            {tr("保存")}{' '}{pending.size > 0 ? tr(" · {0} 处改动", { 0: pending.size }) : ''}
          </button>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-500 hover:bg-slate-800/60 hover:text-slate-200"
          >
            <X size={15} />
          </button>
        </div>
      </header>

      {error && (
        <div className="border-b border-rose-500/30 bg-rose-500/10 px-5 py-2 text-[12px] text-rose-200">
          {error}
        </div>
      )}
      {cleanupWarning && (
        <div className="border-b border-amber-500/30 bg-amber-500/10 px-5 py-2 text-[12px] text-amber-100" data-testid="paper-category-cleanup-warning">
          <p>{cleanupWarning}</p>
          {pendingCleanupId && (
            <button
              type="button"
              data-testid="paper-category-cleanup-retry"
              disabled={busy === 'cleanup-retry'}
              onClick={() =>
                void runManage('cleanup-retry', async () => {
                  const result = await retryPaperCategoryCleanup(pendingCleanupId)
                  if (result.cleanup_status === 'incomplete') {
                    setCleanupWarning((result.warnings && result.warnings.join(' ')) || cleanupWarning)
                    setPendingCleanupId(result.pending_cleanup?.id ?? pendingCleanupId)
                    return
                  }
                  setCleanupWarning(null)
                  setPendingCleanupId(null)
                })
              }
              className="mt-2 rounded-md bg-amber-600 px-2 py-1 text-[11px] text-white hover:bg-amber-500 disabled:opacity-40"
            >
              {tr("重试未完成的本地文件清理")}
            </button>
          )}
        </div>
      )}

      {loading ? (
        <div className="flex flex-1 items-center justify-center text-slate-500">
          <Loader2 size={18} className="mr-2 animate-spin" /> {tr("加载中…")}</div>
      ) : (
        <div className="flex min-h-0 flex-1">
          {/* ── LEFT: categories (manage) ──────────────────────── */}
          <aside className="flex w-80 shrink-0 flex-col border-r border-slate-800 bg-[var(--surface-0d1016)]">
            <div className="border-b border-slate-800/70 px-4 py-2 text-[11px] font-medium text-slate-400">
              {tr("大类（点击跳到该组）")}</div>
            <div className="min-h-0 flex-1 overflow-y-auto p-2 space-y-1">
              {cats.map(cat => {
                const isEditing = editing === cat.name
                const rowBusy =
                  busy === `rename:${cat.name}` || busy === `delete:${cat.name}`
                const count = grouped.get(cat.name)?.length ?? 0
                return (
                  <div
                    key={cat.name}
                    data-testid={`paper-category-row-${cat.name}`}
                    className="rounded-lg border border-slate-800 bg-slate-900/30 px-2.5 py-2"
                  >
                    <div className="flex items-center gap-2">
                      {isEditing ? (
                        <>
                          <input
                            autoFocus
                            aria-label={tr("新的大类名称")}
                            value={editName}
                            onChange={e => setEditName(e.target.value)}
                            onKeyDown={e => {
                              if (e.key === 'Enter') {
                                const to = editName.trim()
                                if (!to || to === cat.name) return
                                const conflict = conflictName(to, cat.name)
                                if (conflict) { setError(conflict); return }
                                setRenameConfirm({ from: cat.name, to })
                              }
                              if (e.key === 'Escape') setEditing(null)
                            }}
                            className="min-w-0 flex-1 rounded border border-indigo-500/50 bg-slate-950/70 px-1.5 py-0.5 text-[12px] text-slate-100 focus:outline-none"
                          />
                          <button
                            type="button"
                            aria-label={tr("提出重命名")}
                            onClick={() => {
                              const to = editName.trim()
                              if (!to || to === cat.name) { setEditing(null); return }
                              const conflict = conflictName(to, cat.name)
                              if (conflict) { setError(conflict); return }
                              setRenameConfirm({ from: cat.name, to })
                            }}
                            disabled={rowBusy}
                            className="text-emerald-300 hover:text-emerald-200"
                          >
                            {rowBusy ? <Loader2 size={12} className="animate-spin" /> : <Check size={13} />}
                          </button>
                          <button type="button" aria-label={tr("取消重命名")} onClick={() => setEditing(null)} className="text-slate-500 hover:text-slate-300">
                            <X size={13} />
                          </button>
                        </>
                      ) : (
                        <>
                          <button onClick={() => scrollToLane(cat.name)} className="min-w-0 flex-1 text-left">
                            <span className="text-[12px] font-medium text-slate-100">{cat.name}</span>
                            {!cat.removable && <span className="ml-1 text-[9px] text-slate-500">{tr("保留")}</span>}
                          </button>
                          <span className="tabular-nums text-[11px] text-slate-500">{count}</span>
                          {cat.removable && (
                            <>
                              <button
                                type="button"
                                aria-label={tr("重命名 {0}", { 0: cat.name })}
                                onClick={() => {
                                  setEditing(cat.name)
                                  setEditName(cat.name)
                                }}
                                title={tr("重命名（需确认旧名与新名；该类论文会一并更新）")}
                                className="text-slate-500 hover:text-indigo-200"
                              >
                                <Pencil size={11} />
                              </button>
                              <button
                                type="button"
                                data-testid={`paper-category-delete-${cat.name}`}
                                aria-label={tr("删除大类 {0}", { 0: cat.name })}
                                onClick={() => {
                                  const count = cat.count ?? grouped.get(cat.name)?.length ?? 0
                                  setDeleteTarget('')
                                  setConfirmResearch(false)
                                  setConfirmCanvases(false)
                                  setDeletePreview(null)
                                  setDeleteConfirm({ name: cat.name, count })
                                  void runManage(`preview:${cat.name}`, async () => {
                                    const preview = await previewPaperCategoryDeletion(cat.name)
                                    setDeletePreview(preview)
                                    setDeleteConfirm({ name: cat.name, count: preview.papers.count })
                                  })
                                }}
                                title={tr("删除大类（不会删除论文；有论文时需先转移到其他大类）")}
                                className="text-slate-500 hover:text-rose-300"
                              >
                                <Trash2 size={11} />
                              </button>
                            </>
                          )}
                        </>
                      )}
                    </div>

                  </div>
                )
              })}
            </div>

            {/* add category */}
            <div className="border-t border-slate-800/70 p-2">
              <div className="flex items-center gap-1.5">
                <input
                  data-testid="paper-category-add-input"
                  aria-label={tr("新增大类名称")}
                  value={newName}
                  onChange={e => setNewName(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && newName.trim()) {
                      const conflict = conflictName(newName)
                      if (conflict) { setError(conflict); return }
                      void runManage('add', async () => {
                        await addPaperCategory(newName.trim())
                        setNewName('')
                      })
                    }
                    if (e.key === 'Escape') setNewName('')
                  }}
                  placeholder={tr("新增大类…")}
                  className="min-w-0 flex-1 rounded border border-slate-700 bg-slate-950/70 px-2 py-1 text-[12px] text-slate-100 focus:border-indigo-500/60 focus:outline-none"
                />
                <button
                  type="button"
                  data-testid="paper-category-add-submit"
                  aria-label={tr("新增大类")}
                  onClick={() => {
                    const conflict = conflictName(newName)
                    if (conflict) { setError(conflict); return }
                    void runManage('add', async () => {
                      await addPaperCategory(newName.trim())
                      setNewName('')
                    })
                  }}
                  disabled={busy === 'add' || !newName.trim()}
                  className="inline-flex items-center gap-1 rounded bg-indigo-500 px-2 py-1 text-[12px] text-white hover:bg-indigo-400 disabled:opacity-50"
                >
                  {busy === 'add' ? <Loader2 size={11} className="animate-spin" /> : <Plus size={11} />}
                </button>
              </div>
            </div>
          </aside>

          {/* ── RIGHT: collapsible list grouped by category ─────── */}
          <main className="relative flex min-h-0 flex-1 flex-col">
            {/* toolbar — move bar when cards are selected, else expand/collapse-all */}
            {selected.size > 0 ? (
              <div className="z-10 flex flex-wrap items-center gap-2 border-b border-indigo-500/30 bg-[var(--surface-11131b)] px-4 py-2">
                <span className="text-[12px] text-indigo-100">{tr("已选")}{' '}{selected.size} {tr("篇")}</span>
                <ArrowRight size={13} className="text-slate-500" />
                <span className="text-[11px] text-slate-400">{tr("移到：")}</span>
                <button
                  onClick={() => moveSelectedTo(INHERIT)}
                  className="rounded border border-slate-600 bg-slate-800/70 px-2 py-0.5 text-[11px] text-slate-200 hover:bg-slate-700"
                >
                  {tr("跟随模型")}</button>
                {catNames.map(n => (
                  <button
                    key={n}
                    onClick={() => moveSelectedTo(n)}
                    className="rounded border border-indigo-500/40 bg-indigo-500/10 px-2 py-0.5 text-[11px] text-indigo-100 hover:bg-indigo-500/20"
                  >
                    {n}
                  </button>
                ))}
                <button
                  onClick={() => setSelected(new Set())}
                  className="ml-auto text-[11px] text-slate-500 hover:text-slate-300"
                >
                  {tr("取消选择")}</button>
              </div>
            ) : (
              <div className="flex items-center gap-3 border-b border-slate-800 px-4 py-2 text-[11px] text-slate-500">
                <span>{tr("共")}{' '}{papers.length} {tr("篇 · 点大类展开，选中论文后移动")}</span>
                <button
                  onClick={() => setExpanded(allExpanded ? new Set() : new Set(catNames))}
                  className="ml-auto rounded border border-slate-700 px-2 py-0.5 text-[11px] text-slate-300 hover:bg-slate-800"
                >
                  {allExpanded ? tr("全部收起") : tr("全部展开")}
                </button>
              </div>
            )}

            <div className="min-h-0 flex-1 overflow-y-auto p-2">
              {catNames.map(name => {
                const lanePapers = grouped.get(name) || []
                const isOpen = expanded.has(name)
                return (
                  <div key={name} id={`lane-${name}`} className="mb-0.5">
                    <button
                      onClick={() => toggleExpand(name)}
                      className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-slate-800/40"
                    >
                      {isOpen ? (
                        <ChevronDown size={14} className="shrink-0 text-slate-500" />
                      ) : (
                        <ChevronRight size={14} className="shrink-0 text-slate-500" />
                      )}
                      <span className="text-[13px] font-semibold text-slate-100">{name}</span>
                      <span className="tabular-nums text-[11px] text-slate-500">{lanePapers.length}</span>
                    </button>
                    {isOpen && (
                      <div className="ml-[11px] mt-0.5 space-y-0.5 border-l border-slate-800 pl-2.5">
                        {lanePapers.length === 0 ? (
                          <div className="px-2 py-1 text-[11px] text-slate-600">{tr("暂无论文")}</div>
                        ) : (
                          lanePapers.map(p => {
                            const pid = String(p.id)
                            const isSel = selected.has(pid)
                            const changed = pending.has(pid)
                            return (
                              <button
                                key={pid}
                                onClick={() => toggleSelect(pid)}
                                className={`flex w-full items-center gap-2 rounded px-2 py-1 text-left text-[12px] transition-colors ${
                                  isSel
                                    ? 'bg-indigo-500/15 text-indigo-100 ring-1 ring-indigo-400/50'
                                    : 'text-slate-300 hover:bg-slate-800/40'
                                }`}
                              >
                                <span
                                  className={`flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border text-[8px] ${
                                    isSel
                                      ? 'border-indigo-300 bg-indigo-400 text-white'
                                      : 'border-slate-600 text-transparent'
                                  }`}
                                >
                                  <Check size={9} />
                                </span>
                                <span className="truncate">{p.title || tr("(无标题)")}</span>
                                {changed && (
                                  <span className="ml-auto shrink-0 text-[10px] text-indigo-300">{tr("● 未保存")}</span>
                                )}
                              </button>
                            )
                          })
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </main>
        </div>
      )}
      {renameConfirm && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 px-4"
          data-testid="paper-category-rename-dialog-backdrop"
          onClick={event => {
            if (event.target === event.currentTarget && busy !== `rename:${renameConfirm.from}`) {
              setRenameConfirm(null)
            }
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="paper-category-rename-title"
            data-testid="paper-category-rename-dialog"
            className="w-full max-w-md rounded-2xl border border-slate-700 bg-slate-900 p-5 shadow-2xl shadow-black/50"
            onClick={event => event.stopPropagation()}
            onKeyDown={event => {
              if (event.key === 'Escape') setRenameConfirm(null)
            }}
          >
            <h3 id="paper-category-rename-title" className="text-base font-semibold text-slate-100">
              {tr("确认重命名论文大类？")}
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-slate-300">
              {tr("将「{0}」重命名为「{1}」。该大类下的论文会一并更新，论文本身不会被删除。", { 0: renameConfirm.from, 1: renameConfirm.to })}
            </p>
            <div className="mt-4 flex justify-end gap-2">
              <button
                type="button"
                data-testid="paper-category-rename-cancel"
                disabled={busy === `rename:${renameConfirm.from}`}
                onClick={() => setRenameConfirm(null)}
                className="rounded-md px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-40"
              >
                {tr("取消")}
              </button>
              <button
                type="button"
                data-testid="paper-category-rename-confirm"
                disabled={busy === `rename:${renameConfirm.from}`}
                onClick={() =>
                  void runManage(`rename:${renameConfirm.from}`, async () => {
                    await renamePaperCategory(renameConfirm.from, renameConfirm.to)
                    setEditing(null)
                    setRenameConfirm(null)
                  })
                }
                className="inline-flex items-center gap-1 rounded-md bg-indigo-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-400 disabled:opacity-40"
              >
                {busy === `rename:${renameConfirm.from}` ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
                {tr("确认重命名")}
              </button>
            </div>
          </div>
        </div>
      )}
      {deleteConfirm && (() => {
        const alternatives = cats.filter(item => item.name !== deleteConfirm.name)
        const paperCount = deletePreview?.papers.count ?? deleteConfirm.count
        const researchCount = deletePreview?.research.count ?? 0
        const canvasCount = deletePreview?.canvases?.count ?? 0
        const contentfulCanvasCount = deletePreview?.canvases?.contentfulCount ?? 0
        const canDeleteEmpty = paperCount === 0 && researchCount === 0 && canvasCount === 0
        const canMigrate = paperCount > 0 && alternatives.length > 0 && Boolean(deleteTarget)
        const blocked = paperCount > 0 && alternatives.length === 0
        const researchBlocked = researchCount > 0 && !confirmResearch
        const canvasBlocked = contentfulCanvasCount > 0 && !confirmCanvases
        const previewBusy = busy === `preview:${deleteConfirm.name}`
        const rowBusy = busy === `delete:${deleteConfirm.name}` || previewBusy
        return (
          <div
            className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 px-4"
            data-testid="paper-category-delete-dialog-backdrop"
            onClick={event => {
              if (event.target === event.currentTarget && !rowBusy) {
                setDeleteConfirm(null)
                setDeletePreview(null)
                setConfirmResearch(false)
                setConfirmCanvases(false)
                setDeleteTarget('')
              }
            }}
          >
            <div
              role="dialog"
              aria-modal="true"
              aria-labelledby="paper-category-delete-title"
              data-testid="paper-category-delete-dialog"
              className="w-full max-w-lg rounded-2xl border border-slate-700 bg-slate-900 p-5 shadow-2xl shadow-black/50"
              onClick={event => event.stopPropagation()}
              onKeyDown={event => {
                if (event.key === 'Escape' && !rowBusy) {
                  setDeleteConfirm(null)
                  setDeletePreview(null)
                  setConfirmResearch(false)
                  setConfirmCanvases(false)
                  setDeleteTarget('')
                }
              }}
            >
              <h3 id="paper-category-delete-title" className="text-base font-semibold text-slate-100">
                {tr("删除论文大类？")}
              </h3>
              <p className="mt-2 text-sm font-medium text-slate-200">{deleteConfirm.name}</p>
              {canDeleteEmpty && !previewBusy && (
                <p className="mt-2 text-sm leading-relaxed text-slate-400">
                  {tr("该大类目前没有论文，也没有识途调研或索骥画布。确认后只删除这个大类，不会删除任何论文。")}
                </p>
              )}
              {paperCount > 0 && !blocked && (
                <>
                  <p className="mt-2 text-sm leading-relaxed text-slate-400">
                    {tr("该大类下有 {0} 篇论文。必须先选择另一个已有大类，确认转移后再删除。这不会删除论文，也不会自动创建新的目标大类。", { 0: paperCount })}
                  </p>
                  <label className="mt-3 block text-xs text-slate-400" htmlFor="paper-category-delete-target">
                    {tr("论文转移到")}
                  </label>
                  <select
                    id="paper-category-delete-target"
                    data-testid="paper-category-delete-target"
                    value={deleteTarget}
                    onChange={e => setDeleteTarget(e.target.value)}
                    className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-2.5 py-1.5 text-sm text-slate-100 focus:border-indigo-500/60 focus:outline-none"
                  >
                    <option value="">{tr("请选择已有大类")}</option>
                    {alternatives.map(item => (
                      <option key={item.name} value={item.name}>{item.name}</option>
                    ))}
                  </select>
                </>
              )}
              {blocked && (
                <p className="mt-2 text-sm leading-relaxed text-amber-200">
                  {tr("没有其他可用大类。请先新增大类，再将论文转移后删除。不会擅自创建目标大类。")}
                </p>
              )}
              {previewBusy && (
                <p className="mt-2 inline-flex items-center gap-2 text-sm text-slate-400">
                  <Loader2 size={13} className="animate-spin" /> {tr("正在检查关联的识途调研和索骥画布…")}
                </p>
              )}
              {researchCount > 0 && (
                <div className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2" data-testid="paper-category-delete-research">
                  <p className="text-sm leading-relaxed text-amber-100">
                    {tr("该大类还关联 {0} 项识途调研。确认删除后会一并清理这些调研的本地结果；取消则保留大类和调研。不会删除资料库已入库论文。", { 0: researchCount })}
                  </p>
                  <ul className="mt-2 space-y-2">
                    {deletePreview?.research.items.map(item => (
                      <li key={item.boardId} className="text-[12px] leading-relaxed text-slate-200" data-testid={`paper-category-delete-research-${item.boardId}`}>
                        <span className="font-medium">{item.name}</span>
                        {item.keywords.length > 0 && (
                          <span className="text-slate-400"> · {tr("关键词")} {item.keywords.join('、')}</span>
                        )}
                        <span className="text-slate-400"> · {tr("画布卡片")} {item.paperCards}</span>
                        <ul className="mt-1 list-disc pl-4 text-[11px] text-slate-400">
                          {item.willDelete.map(line => <li key={line}>{line}</li>)}
                        </ul>
                        <p className="mt-1 text-[11px] text-slate-500">{tr("将保留：")}{item.willRetain.join(tr("；"))}</p>
                      </li>
                    ))}
                  </ul>
                  <label className="mt-2 flex items-start gap-2 text-[12px] text-slate-100">
                    <input
                      type="checkbox"
                      data-testid="paper-category-delete-research-confirm"
                      checked={confirmResearch}
                      onChange={e => setConfirmResearch(e.target.checked)}
                      className="mt-0.5"
                    />
                    <span>{tr("确认删除以上识途调研及其本地副本。不会迁移调研，也不会删除资料库论文。")}</span>
                  </label>
                </div>
              )}
              {canvasCount > 0 && (
                <div className="mt-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2" data-testid="paper-category-delete-canvases">
                  <p className="text-sm leading-relaxed text-amber-100">
                    {tr("该大类还关联 {0} 个索骥画布。确认删除后这些画布（含手工连线）会一并删除；取消则保留大类和画布。", { 0: canvasCount })}
                  </p>
                  <ul className="mt-2 space-y-2">
                    {deletePreview?.canvases?.items.map(item => (
                      <li key={item.boardId} className="text-[12px] leading-relaxed text-slate-200" data-testid={`paper-category-delete-canvas-${item.boardId}`}>
                        <span className="font-medium">{item.name}</span>
                        <span className="text-slate-400"> · {tr("节点")} {item.nodes} · {tr("连线")} {item.edges}</span>
                        <ul className="mt-1 list-disc pl-4 text-[11px] text-slate-400">
                          {item.willDelete.map(line => <li key={line}>{line}</li>)}
                        </ul>
                      </li>
                    ))}
                  </ul>
                  {contentfulCanvasCount > 0 && (
                    <label className="mt-2 flex items-start gap-2 text-[12px] text-slate-100">
                      <input
                        type="checkbox"
                        data-testid="paper-category-delete-canvas-confirm"
                        checked={confirmCanvases}
                        onChange={e => setConfirmCanvases(e.target.checked)}
                        className="mt-0.5"
                      />
                      <span>{tr("确认删除以上索骥画布（含节点和手工连线）。不会删除资料库论文。")}</span>
                    </label>
                  )}
                </div>
              )}
              <div className="mt-4 flex justify-end gap-2">
                <button
                  type="button"
                  data-testid="paper-category-delete-cancel"
                  disabled={rowBusy}
                  onClick={() => {
                    setDeleteConfirm(null)
                    setDeletePreview(null)
                    setConfirmResearch(false)
                    setConfirmCanvases(false)
                  }}
                  className="rounded-md px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-40"
                >
                  {tr("取消")}
                </button>
                <button
                  type="button"
                  data-testid="paper-category-delete-confirm"
                  disabled={rowBusy || blocked || researchBlocked || canvasBlocked || (paperCount > 0 && !canMigrate) || !deletePreview}
                  onClick={() =>
                    void runManage(`delete:${deleteConfirm.name}`, async () => {
                      try {
                        const result = await deletePaperCategory(
                          deleteConfirm.name,
                          paperCount > 0 ? deleteTarget : undefined,
                          {
                            confirmResearch: researchCount > 0,
                            confirmCanvases: contentfulCanvasCount > 0,
                            researchFingerprint: deletePreview?.research.fingerprint,
                          },
                        )
                        setDeleteConfirm(null)
                        setDeleteTarget('')
                        setConfirmResearch(false)
                        setConfirmCanvases(false)
                        setDeletePreview(null)
                        if (result.cleanup_status === 'incomplete') {
                          setCleanupWarning((result.warnings && result.warnings.join(' ')) || tr("大类已删除，但部分本地文件尚未清理。这次没有回滚。请重试文件清理。"))
                          setPendingCleanupId(result.pending_cleanup?.id ?? null)
                        } else {
                          setCleanupWarning(null)
                          setPendingCleanupId(null)
                        }
                      } catch (error) {
                        const next = previewFromError(error)
                        if (next) {
                          setDeletePreview(next)
                          setConfirmResearch(false)
                          setConfirmCanvases(false)
                        }
                        throw error
                      }
                    })
                  }
                  className="inline-flex items-center gap-1 rounded-md bg-rose-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-rose-500 disabled:cursor-not-allowed disabled:bg-rose-900 disabled:text-rose-200"
                >
                  {rowBusy ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
                  {tr("删除大类")}
                </button>
              </div>
            </div>
          </div>
        )
      })()}
    </div>
  )
}
