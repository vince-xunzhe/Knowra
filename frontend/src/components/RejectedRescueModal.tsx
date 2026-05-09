import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  X,
  Loader2,
  Search,
  RotateCcw,
  CheckSquare,
  Square,
  ChevronDown,
  ChevronRight,
} from 'lucide-react'
import {
  bulkUpdatePromotion,
  listPromotionCandidates,
  updatePromotionStatus,
  type PromotionCandidate,
} from '../api/client'

interface Props {
  open: boolean
  onClose: () => void
  onRecalled: (firstRecalledId: number) => void
}

const TYPE_LABELS: Record<string, string> = {
  technique: '方法',
  dataset: '数据集',
  problem_area: '研究领域',
  concept: '概念',
}

const REJECTOR_LABELS: Record<string, { label: string; hint: string }> = {
  user: {
    label: '你淘汰的',
    hint: '通常不需要再看 —— 你之前明确说不要',
  },
  llm: {
    label: 'LLM 淘汰的',
    hint: '可能有误判，值得抽查',
  },
  heuristic: {
    label: '启发式淘汰的',
    hint: '空标题、纯数字或没有来源 —— 一般是噪音',
  },
  legacy: {
    label: '历史淘汰',
    hint: '迁移自旧 hidden 标记',
  },
  unknown: {
    label: '未标记',
    hint: '迁移或异常路径产生',
  },
}

/**
 * Rescue UI for rejected candidates.
 *
 * Structure: top-level groups by *who rejected this* (user / LLM / heuristic
 * / legacy), then a secondary chip row per node_type within each group.
 * That ordering matches the user's mental triage ("LLM might be wrong;
 * heuristic rejects are usually correct"). Within each group we expose a
 * bulk recall button so you don't have to click 80 times.
 */
export default function RejectedRescueModal({ open, onClose, onRecalled }: Props) {
  const [items, setItems] = useState<PromotionCandidate[]>([])
  const [loading, setLoading] = useState(false)
  const [busyIds, setBusyIds] = useState<Set<number>>(new Set())
  const [groupBusy, setGroupBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  // Collapsed group keys; format is `rej` for top-level, `rej:typ` for
  // sub-groups. Persisted across renders within a single open of the
  // modal — every fresh open starts with everything expanded.
  const [collapsedKeys, setCollapsedKeys] = useState<Set<string>>(new Set())

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await listPromotionCandidates('rejected', 1000)
      setItems(data.items)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) {
      setSelected(new Set())
      setCollapsedKeys(new Set())
      void load()
    }
  }, [open, load])

  const toggleCollapsed = useCallback((key: string) => {
    setCollapsedKeys(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return items
    return items.filter(it =>
      it.title.toLowerCase().includes(q) ||
      (it.tags || []).some(t => t.toLowerCase().includes(q)) ||
      (it.promotion_reason || '').toLowerCase().includes(q),
    )
  }, [items, query])

  // Two-level grouping for the triage flow.
  const groups = useMemo(() => {
    const byRejector: Record<string, Record<string, PromotionCandidate[]>> = {}
    for (const item of filtered) {
      const rej = item.promoted_by || 'unknown'
      const typ = item.node_type || 'other'
      if (!byRejector[rej]) byRejector[rej] = {}
      if (!byRejector[rej][typ]) byRejector[rej][typ] = []
      byRejector[rej][typ].push(item)
    }
    return byRejector
  }, [filtered])

  // Stable order: LLM first (most likely to need review), then user, heuristic,
  // legacy, anything else by name.
  const orderedRejectors = useMemo(() => {
    const order = ['llm', 'user', 'heuristic', 'legacy']
    const known = order.filter(k => groups[k])
    const rest = Object.keys(groups).filter(k => !order.includes(k)).sort()
    return [...known, ...rest]
  }, [groups])

  const recall = useCallback(
    async (node: PromotionCandidate) => {
      setBusyIds(prev => new Set(prev).add(node.id))
      try {
        await updatePromotionStatus(node.id, 'pending')
        setItems(prev => prev.filter(it => it.id !== node.id))
        setSelected(prev => {
          const copy = new Set(prev)
          copy.delete(node.id)
          return copy
        })
        onRecalled(node.id)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setBusyIds(prev => {
          const copy = new Set(prev)
          copy.delete(node.id)
          return copy
        })
      }
    },
    [onRecalled],
  )

  const recallGroup = useCallback(
    async (rejector: string, type?: string) => {
      const ids = filtered
        .filter(it =>
          (it.promoted_by || 'unknown') === rejector &&
          (type ? it.node_type === type : true),
        )
        .map(it => it.id)
      if (ids.length === 0) return
      const groupKey = `${rejector}:${type || 'all'}`
      setGroupBusy(groupKey)
      try {
        await bulkUpdatePromotion(ids, 'pending')
        setItems(prev => prev.filter(it => !ids.includes(it.id)))
        if (ids[0] !== undefined) onRecalled(ids[0])
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        setGroupBusy(null)
      }
    },
    [filtered, onRecalled],
  )

  const recallSelected = useCallback(async () => {
    const ids = Array.from(selected)
    if (ids.length === 0) return
    setGroupBusy('selection')
    try {
      await bulkUpdatePromotion(ids, 'pending')
      setItems(prev => prev.filter(it => !selected.has(it.id)))
      setSelected(new Set())
      if (ids[0] !== undefined) onRecalled(ids[0])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setGroupBusy(null)
    }
  }, [selected, onRecalled])

  const toggleSelect = useCallback((id: number) => {
    setSelected(prev => {
      const copy = new Set(prev)
      if (copy.has(id)) copy.delete(id)
      else copy.add(id)
      return copy
    })
  }, [])

  if (!open) return null

  return (
    <div className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm flex items-center justify-center p-6">
      <div className="w-full max-w-3xl max-h-[80vh] bg-[#0f1117] border border-slate-800 rounded-2xl shadow-2xl flex flex-col">
        <header className="px-5 py-4 border-b border-slate-800 flex items-start gap-3">
          <div className="flex-1 min-w-0">
            <h2 className="text-base font-semibold text-white">召回已淘汰节点</h2>
            <p className="text-[12px] text-slate-500 mt-1">
              按淘汰来源分组：LLM 的判定最可能误伤，启发式的几乎都是噪音。点节点 → 召回到待评审；
              选中多个 → 整批召回。
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-slate-500 hover:text-white p-1.5 -mr-1 rounded-lg hover:bg-slate-800/60"
          >
            <X size={16} />
          </button>
        </header>

        <div className="px-5 py-3 flex flex-wrap items-center gap-3 border-b border-slate-800">
          <div className="flex-1 min-w-[14rem] relative">
            <Search size={12} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="按标题 / 标签 / 理由过滤"
              className="w-full pl-8 pr-3 py-1.5 text-[12px] bg-slate-900 border border-slate-800 rounded-lg text-slate-200 placeholder-slate-600 focus:outline-none focus:border-slate-700"
            />
          </div>
          {selected.size > 0 && (
            <button
              onClick={recallSelected}
              disabled={groupBusy === 'selection'}
              className="inline-flex items-center gap-1.5 bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-200 border border-emerald-500/40 text-[12px] px-3 py-1.5 rounded-lg disabled:opacity-50 transition-colors"
            >
              {groupBusy === 'selection' ? (
                <Loader2 size={12} className="animate-spin" />
              ) : (
                <RotateCcw size={12} />
              )}
              召回选中 ({selected.size})
            </button>
          )}
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-3">
          {error && (
            <div className="mb-3 px-3 py-2 rounded-lg border border-rose-500/40 bg-rose-500/10 text-rose-200 text-[12px]">
              {error}
            </div>
          )}
          {loading ? (
            <div className="py-12 flex items-center justify-center text-slate-500 text-[12px]">
              <Loader2 size={12} className="animate-spin mr-2" /> 加载中…
            </div>
          ) : filtered.length === 0 ? (
            <div className="py-12 text-center text-slate-500 text-[12px]">
              没有匹配的已淘汰节点。
            </div>
          ) : (
            orderedRejectors.map(rej => {
              const types = groups[rej]
              if (!types) return null
              const meta = REJECTOR_LABELS[rej] || REJECTOR_LABELS.unknown
              const typeKeys = Object.keys(types).sort()
              const groupTotal = typeKeys.reduce((sum, k) => sum + types[k].length, 0)
              const groupCollapsed = collapsedKeys.has(rej)
              return (
                <section key={rej} className="mb-5">
                  <header className="flex items-baseline justify-between gap-3 mb-2">
                    <button
                      onClick={() => toggleCollapsed(rej)}
                      className="min-w-0 text-left flex items-start gap-1.5 hover:opacity-80 transition-opacity"
                    >
                      <span className="mt-0.5 text-slate-500">
                        {groupCollapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
                      </span>
                      <span className="min-w-0">
                        <h3 className="text-[13px] font-medium text-slate-200">
                          {meta.label}{' '}
                          <span className="text-slate-500 tabular-nums text-[11px]">
                            {groupTotal}
                          </span>
                        </h3>
                        <p className="text-[11px] text-slate-500 mt-0.5">{meta.hint}</p>
                      </span>
                    </button>
                    <button
                      onClick={() => recallGroup(rej)}
                      disabled={groupBusy === `${rej}:all`}
                      className="text-[11px] text-emerald-300 hover:text-emerald-200 px-2 py-1 rounded-md hover:bg-emerald-500/10 transition-colors inline-flex items-center gap-1 disabled:opacity-50 shrink-0"
                    >
                      {groupBusy === `${rej}:all` ? (
                        <Loader2 size={10} className="animate-spin" />
                      ) : (
                        <RotateCcw size={10} />
                      )}
                      整组召回
                    </button>
                  </header>

                  {!groupCollapsed && typeKeys.map(typ => {
                    const list = types[typ]
                    const subKey = `${rej}:${typ}`
                    const subCollapsed = collapsedKeys.has(subKey)
                    return (
                      <div key={typ} className="mb-3 ml-4">
                        <div className="flex items-center gap-2 mb-1.5">
                          <button
                            onClick={() => toggleCollapsed(subKey)}
                            className="inline-flex items-center gap-1.5 text-[11px] text-slate-400 hover:text-slate-200 transition-colors"
                          >
                            <span className="text-slate-500">
                              {subCollapsed ? <ChevronRight size={11} /> : <ChevronDown size={11} />}
                            </span>
                            <span>{TYPE_LABELS[typ] || typ}</span>
                            <span className="text-slate-600">{list.length}</span>
                          </button>
                          <button
                            onClick={() => recallGroup(rej, typ)}
                            disabled={groupBusy === `${rej}:${typ}`}
                            className="text-[10.5px] text-slate-500 hover:text-emerald-300 transition-colors inline-flex items-center gap-1 disabled:opacity-50"
                          >
                            {groupBusy === `${rej}:${typ}` ? (
                              <Loader2 size={9} className="animate-spin" />
                            ) : (
                              <RotateCcw size={9} />
                            )}
                            召回此类
                          </button>
                        </div>
                        {!subCollapsed && (
                          <ul className="space-y-1">
                            {list.map(item => (
                              <RescueRow
                                key={item.id}
                                item={item}
                                busy={busyIds.has(item.id)}
                                selected={selected.has(item.id)}
                                onToggle={() => toggleSelect(item.id)}
                                onRecall={() => recall(item)}
                              />
                            ))}
                          </ul>
                        )}
                      </div>
                    )
                  })}
                </section>
              )
            })
          )}
        </div>
      </div>
    </div>
  )
}

function RescueRow({
  item,
  busy,
  selected,
  onToggle,
  onRecall,
}: {
  item: PromotionCandidate
  busy: boolean
  selected: boolean
  onToggle: () => void
  onRecall: () => void
}) {
  return (
    <li className="flex items-start gap-2 px-2.5 py-2 rounded-lg border border-slate-800 hover:border-slate-700 bg-slate-950/40">
      <button
        onClick={onToggle}
        className="text-slate-500 hover:text-slate-200 mt-0.5"
        title={selected ? '取消选择' : '选择'}
      >
        {selected ? <CheckSquare size={13} className="text-emerald-300" /> : <Square size={13} />}
      </button>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[12.5px] text-slate-200 text-safe-wrap">{item.title}</span>
          <span className="text-[10px] text-slate-500">
            引用 {item.source_paper_ids.length}
          </span>
        </div>
        {item.promotion_reason && (
          <p className="mt-0.5 text-[11px] text-slate-500 leading-relaxed">
            {item.promotion_reason}
          </p>
        )}
      </div>
      <button
        onClick={onRecall}
        disabled={busy}
        className="text-[11px] text-emerald-300 hover:text-emerald-200 hover:bg-emerald-500/10 px-2 py-1 rounded-md inline-flex items-center gap-1 disabled:opacity-50 transition-colors"
      >
        {busy ? <Loader2 size={10} className="animate-spin" /> : <RotateCcw size={10} />}
        召回
      </button>
    </li>
  )
}
