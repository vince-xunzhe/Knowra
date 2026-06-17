import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Users2, Plus, Trash2, Pencil, Check, X, Loader2, Save, RotateCw, ArrowRight,
  ChevronRight, ChevronDown, RefreshCw,
} from 'lucide-react'
import {
  listPaperTeams, addPaperTeam, updatePaperTeamRegistry, deletePaperTeam,
  recomputePaperTeams, bulkSetPaperTeam, listPapers,
  type PaperTeamItem, type PaperRecord,
} from '../api/client'

// Sentinel for "follow author auto-match" (clears the manual override).
const INHERIT = '__inherit__'
const OTHERS = 'others'

function apiErr(e: unknown): string {
  const x = e as { response?: { data?: { detail?: string } }; message?: string }
  return x?.response?.data?.detail || x?.message || String(e)
}

function parseAuthors(s: string): string[] {
  return s
    .split(/[,，\n]/)
    .map(a => a.trim())
    .filter(Boolean)
}

/**
 * 编排团队 — the team dimension's taxonomy + assignment editor, a sibling of
 * 编排大类 (CategoryComposer). The big difference: each team carries a list of
 * core authors. A paper is auto-assigned to a team when its authors match, so
 * editing a team's authors (and 重算) is the primary workflow; the per-paper
 * board move (override) is for exceptions.
 *
 * Left: teams (add / rename / edit authors / delete) + 重算.
 * Right: a collapsible board — one lane per team plus "others", select papers
 * and move them to a team (override) or back to author-match. Saves together.
 */
export default function TeamComposer({ onClose }: { onClose: () => void }) {
  const [teams, setTeams] = useState<PaperTeamItem[]>([])
  const [othersCount, setOthersCount] = useState(0)
  const [papers, setPapers] = useState<PaperRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const [pending, setPending] = useState<Map<string, string>>(new Map())
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  const [newName, setNewName] = useState('')
  const [editing, setEditing] = useState<string | null>(null)
  const [editName, setEditName] = useState('')
  const [editAuthors, setEditAuthors] = useState('')
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)

  const reload = useCallback(async () => {
    const [t, p] = await Promise.all([listPaperTeams(), listPapers()])
    setTeams(t.teams)
    setOthersCount(t.others_count)
    setPapers(p)
  }, [])

  useEffect(() => {
    reload()
      .catch(e => setError(apiErr(e)))
      .finally(() => setLoading(false))
  }, [reload])

  const teamNames = useMemo(() => teams.map(t => t.name), [teams])
  // Lanes shown on the right: real teams in registry order, then "others".
  const laneNames = useMemo(() => [...teamNames, OTHERS], [teamNames])

  const shownTeam = useCallback(
    (p: PaperRecord): string => {
      const staged = pending.get(String(p.id))
      if (staged !== undefined) {
        if (staged === INHERIT) return p.paper_team_model || OTHERS
        return staged
      }
      return p.paper_team || OTHERS
    },
    [pending],
  )

  const grouped = useMemo(() => {
    const m = new Map<string, PaperRecord[]>()
    for (const c of laneNames) m.set(c, [])
    for (const p of papers) {
      const c = shownTeam(p)
      if (!m.has(c)) m.set(c, [])
      m.get(c)!.push(p)
    }
    return m
  }, [papers, laneNames, shownTeam])

  const toggleSelect = (id: string) =>
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const moveSelectedTo = (target: string) => {
    setPending(prev => {
      const next = new Map(prev)
      for (const pid of selected) {
        const p = papers.find(x => String(x.id) === pid)
        if (!p) continue
        const current = p.paper_team_override || INHERIT
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

  const startEdit = (t: PaperTeamItem) => {
    setEditing(t.name)
    setEditName(t.name)
    setEditAuthors((t.authors || []).join(', '))
  }

  const saveEdit = (original: string) =>
    void runManage(`edit:${original}`, async () => {
      const newName = editName.trim()
      await updatePaperTeamRegistry(original, {
        new_name: newName && newName !== original ? newName : undefined,
        authors: parseAuthors(editAuthors),
      })
      setEditing(null)
    })

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
        await bulkSetPaperTeam(ids, val === INHERIT ? null : val)
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
  const allExpanded = laneNames.length > 0 && laneNames.every(n => expanded.has(n))

  const scrollToLane = (name: string) => {
    setExpanded(prev => new Set(prev).add(name))
    setTimeout(
      () => document.getElementById(`team-lane-${name}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }),
      0,
    )
  }

  const laneLabel = (name: string) => (name === OTHERS ? 'others（未匹配）' : name)

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-[#0b0d12]">
      <header className="flex items-center gap-3 border-b border-slate-800 bg-[#0f1117] px-5 py-3">
        <Users2 size={15} className="text-indigo-300" />
        <h2 className="text-sm font-semibold text-white">编排团队</h2>
        <span className="text-[11px] text-slate-500">
          按核心作者自动归队；编辑作者后「重算」即可。右侧可手动把论文移入某队
        </span>
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => void runManage('recompute', async () => { await recomputePaperTeams() })}
            disabled={busy === 'recompute' || saving}
            title="按当前作者名单重新归队所有论文"
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-900/60 px-3 py-1.5 text-[12px] text-slate-300 hover:bg-slate-800 disabled:opacity-40"
          >
            {busy === 'recompute' ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            重算
          </button>
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
          {/* ── LEFT: teams (manage + authors) ─────────────────── */}
          <aside className="flex w-96 shrink-0 flex-col border-r border-slate-800 bg-[#0d1016]">
            <div className="border-b border-slate-800/70 px-4 py-2 text-[11px] font-medium text-slate-400">
              团队（核心作者命中即自动归队）
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-2 space-y-1">
              {teams.map(team => {
                const isEditing = editing === team.name
                const rowBusy = busy === `edit:${team.name}` || busy === `delete:${team.name}`
                const count = grouped.get(team.name)?.length ?? 0
                return (
                  <div key={team.name} className="rounded-lg border border-slate-800 bg-slate-900/30 px-2.5 py-2">
                    {isEditing ? (
                      <div className="space-y-1.5">
                        <input
                          autoFocus
                          value={editName}
                          onChange={e => setEditName(e.target.value)}
                          placeholder="团队名"
                          className="w-full rounded border border-indigo-500/50 bg-slate-950/70 px-1.5 py-1 text-[12px] text-slate-100 focus:outline-none"
                        />
                        <textarea
                          value={editAuthors}
                          onChange={e => setEditAuthors(e.target.value)}
                          placeholder="核心作者，逗号分隔（如：Kaiming He, Ross Girshick）"
                          rows={2}
                          className="w-full resize-none rounded border border-slate-700 bg-slate-950/70 px-1.5 py-1 text-[11px] text-slate-200 focus:border-indigo-500/60 focus:outline-none"
                        />
                        <div className="flex items-center justify-end gap-2">
                          <button onClick={() => setEditing(null)} className="text-[11px] text-slate-500 hover:text-slate-300">
                            取消
                          </button>
                          <button
                            onClick={() => saveEdit(team.name)}
                            disabled={rowBusy}
                            className="inline-flex items-center gap-1 rounded bg-indigo-500 px-2 py-0.5 text-[11px] text-white hover:bg-indigo-400 disabled:opacity-50"
                          >
                            {rowBusy ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />}
                            保存并重算
                          </button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <div className="flex items-center gap-2">
                          <button onClick={() => scrollToLane(team.name)} className="min-w-0 flex-1 text-left">
                            <span className="text-[12px] font-medium text-slate-100">{team.name}</span>
                            {team.builtin && <span className="ml-1 text-[9px] text-slate-500">内置</span>}
                          </button>
                          <span className="tabular-nums text-[11px] text-slate-500">{count}</span>
                          <button onClick={() => startEdit(team)} title="编辑名称 / 核心作者" className="text-slate-500 hover:text-indigo-200">
                            <Pencil size={11} />
                          </button>
                          {confirmDelete === team.name ? (
                            <span className="flex items-center gap-1">
                              <button
                                onClick={() =>
                                  void runManage(`delete:${team.name}`, async () => {
                                    await deletePaperTeam(team.name)
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
                              onClick={() => setConfirmDelete(team.name)}
                              title="删除（该队论文回退为 others / 跟随匹配）"
                              className="text-slate-500 hover:text-rose-300"
                            >
                              <Trash2 size={11} />
                            </button>
                          )}
                        </div>
                        {team.authors.length > 0 && (
                          <div className="mt-1 truncate text-[10px] text-slate-500">
                            {team.authors.join(' · ')}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                )
              })}
              <div className="rounded-lg border border-dashed border-slate-800 px-2.5 py-2 text-[11px] text-slate-500">
                others（未匹配）<span className="ml-1 tabular-nums">{grouped.get(OTHERS)?.length ?? othersCount}</span>
              </div>
            </div>

            <div className="border-t border-slate-800/70 p-2">
              <div className="flex items-center gap-1.5">
                <input
                  value={newName}
                  onChange={e => setNewName(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && newName.trim())
                      void runManage('add', async () => {
                        await addPaperTeam(newName.trim())
                        setNewName('')
                      })
                  }}
                  placeholder="新增团队…（先建名，再编辑作者）"
                  className="min-w-0 flex-1 rounded border border-slate-700 bg-slate-950/70 px-2 py-1 text-[12px] text-slate-100 focus:border-indigo-500/60 focus:outline-none"
                />
                <button
                  onClick={() =>
                    void runManage('add', async () => {
                      await addPaperTeam(newName.trim())
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

          {/* ── RIGHT: collapsible board grouped by team ────────── */}
          <main className="relative flex min-h-0 flex-1 flex-col">
            {selected.size > 0 ? (
              <div className="z-10 flex flex-wrap items-center gap-2 border-b border-indigo-500/30 bg-[#11131b] px-4 py-2">
                <span className="text-[12px] text-indigo-100">已选 {selected.size} 篇</span>
                <ArrowRight size={13} className="text-slate-500" />
                <span className="text-[11px] text-slate-400">移到：</span>
                <button
                  onClick={() => moveSelectedTo(INHERIT)}
                  className="rounded border border-slate-600 bg-slate-800/70 px-2 py-0.5 text-[11px] text-slate-200 hover:bg-slate-700"
                >
                  跟随作者匹配
                </button>
                {teamNames.map(n => (
                  <button
                    key={n}
                    onClick={() => moveSelectedTo(n)}
                    className="rounded border border-indigo-500/40 bg-indigo-500/10 px-2 py-0.5 text-[11px] text-indigo-100 hover:bg-indigo-500/20"
                  >
                    {n}
                  </button>
                ))}
                <button onClick={() => setSelected(new Set())} className="ml-auto text-[11px] text-slate-500 hover:text-slate-300">
                  取消选择
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-3 border-b border-slate-800 px-4 py-2 text-[11px] text-slate-500">
                <span>共 {papers.length} 篇 · 点团队展开，选中论文后移动</span>
                <button
                  onClick={() => setExpanded(allExpanded ? new Set() : new Set(laneNames))}
                  className="ml-auto rounded border border-slate-700 px-2 py-0.5 text-[11px] text-slate-300 hover:bg-slate-800"
                >
                  {allExpanded ? '全部收起' : '全部展开'}
                </button>
              </div>
            )}

            <div className="min-h-0 flex-1 overflow-y-auto p-2">
              {laneNames.map(name => {
                const lanePapers = grouped.get(name) || []
                const isOpen = expanded.has(name)
                return (
                  <div key={name} id={`team-lane-${name}`} className="mb-0.5">
                    <button
                      onClick={() => toggleExpand(name)}
                      className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-slate-800/40"
                    >
                      {isOpen ? (
                        <ChevronDown size={14} className="shrink-0 text-slate-500" />
                      ) : (
                        <ChevronRight size={14} className="shrink-0 text-slate-500" />
                      )}
                      <span className={`text-[13px] font-semibold ${name === OTHERS ? 'text-slate-400' : 'text-slate-100'}`}>
                        {laneLabel(name)}
                      </span>
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
                                    isSel ? 'border-indigo-300 bg-indigo-400 text-white' : 'border-slate-600 text-transparent'
                                  }`}
                                >
                                  <Check size={9} />
                                </span>
                                <span className="truncate">{p.title || '(无标题)'}</span>
                                {changed && <span className="ml-auto shrink-0 text-[10px] text-indigo-300">● 未保存</span>}
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
