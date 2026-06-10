import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Tags, Plus, Trash2, Pencil, Check, X, Loader2, Save, RotateCw, ArrowRight,
  ChevronRight, ChevronDown,
} from 'lucide-react'
import {
  listPaperCategories, addPaperCategory, renamePaperCategory, deletePaperCategory,
  bulkSetPaperCategory, listPapers,
  type PaperCategoryItem, type PaperRecord,
} from '../api/client'

// Sentinel for "follow the model" (clears the manual override).
const INHERIT = '__inherit__'

function apiErr(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail || x?.message || String(e)
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
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  const reload = useCallback(async () => {
    const [c, p] = await Promise.all([listPaperCategories(), listPapers()])
    setCats(c)
    setPapers(p)
  }, [])

  useEffect(() => {
    reload()
      .catch(e => setError(apiErr(e)))
      .finally(() => setLoading(false))
  }, [reload])

  const catNames = useMemo(() => cats.map(c => c.name), [cats])

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
    <div className="fixed inset-0 z-50 flex flex-col bg-[#0b0d12]">
      {/* header */}
      <header className="flex items-center gap-3 border-b border-slate-800 bg-[#0f1117] px-5 py-3">
        <Tags size={15} className="text-indigo-300" />
        <h2 className="text-sm font-semibold text-white">编排大类</h2>
        <span className="text-[11px] text-slate-500">
          左侧管理大类；右侧选中论文卡片，移到目标大类，保存后生效
        </span>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => setPending(new Map())}
            disabled={pending.size === 0 || saving}
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-1.5 text-[12px] text-slate-300 hover:bg-slate-800 disabled:opacity-40"
          >
            <RotateCw size={12} />
            撤销
          </button>
          <button
            onClick={handleSave}
            disabled={pending.size === 0 || saving}
            className="inline-flex items-center gap-1.5 rounded-lg bg-indigo-500 px-3 py-1.5 text-[12px] font-medium text-white hover:bg-indigo-400 disabled:opacity-40"
          >
            {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
            保存{pending.size > 0 ? ` · ${pending.size} 处改动` : ''}
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

      {loading ? (
        <div className="flex flex-1 items-center justify-center text-slate-500">
          <Loader2 size={18} className="mr-2 animate-spin" /> 加载中…
        </div>
      ) : (
        <div className="flex min-h-0 flex-1">
          {/* ── LEFT: categories (manage) ──────────────────────── */}
          <aside className="flex w-80 shrink-0 flex-col border-r border-slate-800 bg-[#0d1016]">
            <div className="border-b border-slate-800/70 px-4 py-2 text-[11px] font-medium text-slate-400">
              大类（点击跳到该组）
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-2 space-y-1">
              {cats.map(cat => {
                const isEditing = editing === cat.name
                const rowBusy =
                  busy === `rename:${cat.name}` || busy === `delete:${cat.name}`
                const count = grouped.get(cat.name)?.length ?? 0
                return (
                  <div
                    key={cat.name}
                    className="rounded-lg border border-slate-800 bg-slate-900/30 px-2.5 py-2"
                  >
                    <div className="flex items-center gap-2">
                      {isEditing ? (
                        <>
                          <input
                            autoFocus
                            value={editName}
                            onChange={e => setEditName(e.target.value)}
                            onKeyDown={e => {
                              if (e.key === 'Enter' && editName.trim() && editName.trim() !== cat.name)
                                void runManage(`rename:${cat.name}`, async () => {
                                  await renamePaperCategory(cat.name, editName.trim())
                                  setEditing(null)
                                })
                              if (e.key === 'Escape') setEditing(null)
                            }}
                            className="min-w-0 flex-1 rounded border border-indigo-500/50 bg-slate-950/70 px-1.5 py-0.5 text-[12px] text-slate-100 focus:outline-none"
                          />
                          <button
                            onClick={() =>
                              void runManage(`rename:${cat.name}`, async () => {
                                if (editName.trim() && editName.trim() !== cat.name)
                                  await renamePaperCategory(cat.name, editName.trim())
                                setEditing(null)
                              })
                            }
                            disabled={rowBusy}
                            className="text-emerald-300 hover:text-emerald-200"
                          >
                            {rowBusy ? <Loader2 size={12} className="animate-spin" /> : <Check size={13} />}
                          </button>
                          <button onClick={() => setEditing(null)} className="text-slate-500 hover:text-slate-300">
                            <X size={13} />
                          </button>
                        </>
                      ) : (
                        <>
                          <button onClick={() => scrollToLane(cat.name)} className="min-w-0 flex-1 text-left">
                            <span className="text-[12px] font-medium text-slate-100">{cat.name}</span>
                            {!cat.removable && <span className="ml-1 text-[9px] text-slate-500">保留</span>}
                          </button>
                          <span className="tabular-nums text-[11px] text-slate-500">{count}</span>
                          {cat.removable && (
                            <>
                              <button
                                onClick={() => {
                                  setEditing(cat.name)
                                  setEditName(cat.name)
                                }}
                                title="重命名（迁移该类所有论文）"
                                className="text-slate-500 hover:text-indigo-200"
                              >
                                <Pencil size={11} />
                              </button>
                              {confirmDelete === cat.name ? (
                                <span className="flex items-center gap-1">
                                  <button
                                    onClick={() =>
                                      void runManage(`delete:${cat.name}`, async () => {
                                        await deletePaperCategory(cat.name)
                                        setConfirmDelete(null)
                                      })
                                    }
                                    disabled={rowBusy}
                                    className="text-[10px] text-rose-300 hover:text-rose-200"
                                  >
                                    确认
                                  </button>
                                  <button onClick={() => setConfirmDelete(null)} className="text-[10px] text-slate-500">
                                    取消
                                  </button>
                                </span>
                              ) : (
                                <button
                                  onClick={() => setConfirmDelete(cat.name)}
                                  title="删除（该类论文回退为跟随模型）"
                                  className="text-slate-500 hover:text-rose-300"
                                >
                                  <Trash2 size={11} />
                                </button>
                              )}
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
                  value={newName}
                  onChange={e => setNewName(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && newName.trim())
                      void runManage('add', async () => {
                        await addPaperCategory(newName.trim())
                        setNewName('')
                      })
                  }}
                  placeholder="新增大类…"
                  className="min-w-0 flex-1 rounded border border-slate-700 bg-slate-950/70 px-2 py-1 text-[12px] text-slate-100 focus:border-indigo-500/60 focus:outline-none"
                />
                <button
                  onClick={() =>
                    void runManage('add', async () => {
                      await addPaperCategory(newName.trim())
                      setNewName('')
                    })
                  }
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
              <div className="z-10 flex flex-wrap items-center gap-2 border-b border-indigo-500/30 bg-[#11131b] px-4 py-2">
                <span className="text-[12px] text-indigo-100">已选 {selected.size} 篇</span>
                <ArrowRight size={13} className="text-slate-500" />
                <span className="text-[11px] text-slate-400">移到：</span>
                <button
                  onClick={() => moveSelectedTo(INHERIT)}
                  className="rounded border border-slate-600 bg-slate-800/70 px-2 py-0.5 text-[11px] text-slate-200 hover:bg-slate-700"
                >
                  跟随模型
                </button>
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
                  取消选择
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-3 border-b border-slate-800 px-4 py-2 text-[11px] text-slate-500">
                <span>共 {papers.length} 篇 · 点大类展开，选中论文后移动</span>
                <button
                  onClick={() => setExpanded(allExpanded ? new Set() : new Set(catNames))}
                  className="ml-auto rounded border border-slate-700 px-2 py-0.5 text-[11px] text-slate-300 hover:bg-slate-800"
                >
                  {allExpanded ? '全部收起' : '全部展开'}
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
                          <div className="px-2 py-1 text-[11px] text-slate-600">暂无论文</div>
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
                                <span className="truncate">{p.title || '(无标题)'}</span>
                                {changed && (
                                  <span className="ml-auto shrink-0 text-[10px] text-indigo-300">● 未保存</span>
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
    </div>
  )
}
