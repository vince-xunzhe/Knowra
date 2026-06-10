import { useCallback, useEffect, useState } from 'react'
import {
  X,
  Loader2,
  Stethoscope,
  RefreshCw,
  Sparkles,
  Trash2,
  Copy,
  AlertTriangle,
  Check,
  Plus,
} from 'lucide-react'
import {
  runWikiLint,
  getWikiLintStatus,
  recompileConcept,
  updatePromotionStatus,
  acceptLintStub,
  type LintResult,
  type LintReportStatus,
} from '../api/client'

interface Props {
  open: boolean
  onClose: () => void
  /** Called after an apply action mutates the graph (reject/recompile)
   *  so the host can reload. */
  onMutated?: () => void
  /** Adopt a suggested cross-cut concept: create it as a manual concept
   *  seeded with the title / rationale / covered papers. Should throw on
   *  failure so the error surfaces inline. */
  onAdoptConcept?: (seed: {
    title: string
    content: string
    paper_ids: string[]
  }) => Promise<unknown>
}

/**
 * P1 — wiki content health-check. Runs the rule + LLM lint, then lets the
 * user act on each finding inline:
 *   - stub      → 重编译该概念页
 *   - merge     → 淘汰其一（保留另一），去重
 *   - crosscut  → 提示文案（建新概念走 Ask / 手动新增）
 *   - followups → 复制问题到剪贴板，丢进 Ask
 * The durable artifact is data/wiki/lint-report.md (viewable in Obsidian).
 */
export default function WikiLintModal({ open, onClose, onMutated, onAdoptConcept }: Props) {
  const [status, setStatus] = useState<LintReportStatus | null>(null)
  const [result, setResult] = useState<LintResult | null>(null)
  const [running, setRunning] = useState(false)
  const [useLlm, setUseLlm] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [done, setDone] = useState<Set<string>>(new Set())
  // Per-action error keyed by the same key as `done`/`busyId`, so a
  // failed apply shows its reason INLINE under the item the user
  // clicked — instead of a single error banner at the top of the modal
  // that's scrolled out of view (which made failures look like the
  // button "did nothing").
  const [actionErrors, setActionErrors] = useState<Record<string, string>>({})
  const failAction = (key: string, e: unknown) =>
    setActionErrors(prev => ({ ...prev, [key]: e instanceof Error ? e.message : String(e) }))
  const clearAction = (key: string) =>
    setActionErrors(prev => {
      if (!(key in prev)) return prev
      const n = { ...prev }
      delete n[key]
      return n
    })

  useEffect(() => {
    if (!open) return
    getWikiLintStatus().then(setStatus).catch(() => setStatus(null))
  }, [open])

  const handleRun = useCallback(async () => {
    setRunning(true)
    setError(null)
    try {
      const r = await runWikiLint(useLlm)
      setResult(r)
      setStatus({
        exists: true,
        rel_path: r.report_rel_path,
        modified_at: r.generated_at,
      })
    } catch (e: unknown) {
      const msg =
        (e as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail || (e instanceof Error ? e.message : String(e))
      setError(msg)
    } finally {
      setRunning(false)
    }
  }, [useLlm])

  const markDone = (key: string) =>
    setDone(prev => {
      const n = new Set(prev)
      n.add(key)
      return n
    })

  const handleRecompile = useCallback(
    async (conceptId: string) => {
      const key = `stub:${conceptId}`
      setBusyId(key)
      clearAction(key)
      try {
        await recompileConcept(conceptId)
        markDone(key)
        onMutated?.()
      } catch (e) {
        failAction(key, e)
      } finally {
        setBusyId(null)
      }
    },
    [onMutated],
  )

  const handleRejectStub = useCallback(
    async (conceptId: string) => {
      const key = `stub:${conceptId}`
      setBusyId(key)
      clearAction(key)
      try {
        await updatePromotionStatus(
          conceptId,
          'rejected',
          'lint: thin single-source concept, demoted',
        )
        markDone(key)
        onMutated?.()
      } catch (e) {
        failAction(key, e)
      } finally {
        setBusyId(null)
      }
    },
    [onMutated],
  )

  const handleAcceptStub = useCallback(
    async (conceptId: string) => {
      const key = `stub:${conceptId}`
      setBusyId(key)
      clearAction(key)
      try {
        await acceptLintStub(conceptId)
        // No graph mutation — just stops future lint runs flagging it.
        markDone(key)
      } catch (e) {
        failAction(key, e)
      } finally {
        setBusyId(null)
      }
    },
    [],
  )

  const handleRejectDup = useCallback(
    async (keepId: string, dropId: string, pairKey: string) => {
      setBusyId(pairKey)
      clearAction(pairKey)
      try {
        await updatePromotionStatus(
          dropId,
          'rejected',
          `lint: merged into concept #${keepId}`,
        )
        markDone(pairKey)
        onMutated?.()
      } catch (e) {
        failAction(pairKey, e)
      } finally {
        setBusyId(null)
      }
    },
    [onMutated],
  )

  const handleAdopt = useCallback(
    async (
      key: string,
      seed: { title: string; content: string; paper_ids: string[] },
    ) => {
      if (!onAdoptConcept) return
      setBusyId(key)
      clearAction(key)
      try {
        await onAdoptConcept(seed)
        markDone(key)
      } catch (e) {
        failAction(key, e)
      } finally {
        setBusyId(null)
      }
    },
    [onAdoptConcept],
  )

  if (!open) return null

  const j = result?.judgment
  const verdictById = new Map(
    (j?.stubs || []).map(s => [s.concept_id, s]),
  )
  const mergeJudgeByPair = new Map(
    (j?.merges || []).map(m => [`${m.a_id}:${m.b_id}`, m]),
  )

  return (
    <div className="fixed inset-0 z-40 bg-black/55 backdrop-blur-sm flex items-center justify-center p-6">
      <div className="w-full max-w-3xl max-h-[86vh] bg-[#0d1016] border border-slate-800 rounded-xl shadow-2xl flex flex-col">
        <header className="px-5 py-3 border-b border-slate-800/80 flex items-center gap-2">
          <Stethoscope size={14} className="text-indigo-300" />
          <h2 className="text-[13px] font-semibold text-white">Wiki 健康检查</h2>
          {result && (
            <span className="text-[10.5px] text-slate-500">
              扫描 {result.counts.concepts_scanned} 概念 · 待充实{' '}
              {result.counts.stubs} · 可合并 {result.counts.merges} · 待建概念{' '}
              {result.counts.missing_crosscut}
            </span>
          )}
          <button
            onClick={onClose}
            className="ml-auto text-slate-500 hover:text-slate-200 p-1 rounded hover:bg-slate-800/60"
          >
            <X size={13} />
          </button>
        </header>

        <div className="px-5 py-2 border-b border-slate-800/70 flex items-center gap-3 text-[11px]">
          <button
            onClick={handleRun}
            disabled={running}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-indigo-500 hover:bg-indigo-400 text-white font-medium disabled:opacity-50 transition-colors"
          >
            {running ? (
              <Loader2 size={11} className="animate-spin" />
            ) : (
              <RefreshCw size={11} />
            )}
            {running ? '检查中…' : '运行检查'}
          </button>
          <label className="inline-flex items-center gap-1.5 text-slate-400 cursor-pointer">
            <input
              type="checkbox"
              checked={useLlm}
              onChange={e => setUseLlm(e.target.checked)}
              className="accent-indigo-500"
            />
            调用 Agent 判定（关掉则只跑规则层）
          </label>
          {status?.exists && (
            <span className="ml-auto text-[10.5px] text-slate-600">
              报告：{status.rel_path}
            </span>
          )}
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-5 text-[12px]">
          {error && (
            <div className="px-3 py-2 rounded-lg border border-rose-500/40 bg-rose-500/10 text-rose-200">
              {error}
            </div>
          )}

          {!result && !running && (
            <div className="text-center text-slate-500 py-12">
              <Stethoscope size={20} className="mx-auto text-indigo-400/60 mb-3" />
              <p>点「运行检查」扫描存量 wiki 的内容健康度。</p>
              <p className="text-[11px] text-slate-600 mt-1">
                规则层（待充实 / 相似合并 / 待建概念）零 token；Agent 判定一次调用。
              </p>
            </div>
          )}

          {result && (
            <>
              {/* Merge candidates — most actionable, show first */}
              <Section
                title="可合并概念对"
                count={result.merges.length}
                empty="没有发现高相似的概念对"
              >
                {result.merges.map(m => {
                  const jm = mergeJudgeByPair.get(`${m.a_id}:${m.b_id}`)
                  const key = `merge:${m.a_id}:${m.b_id}`
                  const isDone = done.has(key)
                  return (
                    <li
                      key={key}
                      className="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2"
                    >
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-slate-200">{m.a_title}</span>
                        <span className="text-slate-600">⇆</span>
                        <span className="text-slate-200">{m.b_title}</span>
                        <span className="text-[10.5px] text-slate-500 tabular-nums">
                          cos={m.cosine} · 共享 {m.paper_overlap} 篇
                        </span>
                      </div>
                      {jm && (
                        <p className="mt-1 text-[11px] text-slate-400">
                          {jm.should_merge ? '✅ 建议合并' : '➖ 暂不合并'}
                          {jm.keep ? ` · 保留 #${jm.keep}` : ''} — {jm.reason}
                        </p>
                      )}
                      {!isDone ? (
                        <>
                          <div className="mt-1.5 flex gap-2">
                            <ApplyBtn
                              busy={busyId === key}
                              onClick={() => handleRejectDup(m.a_id, m.b_id, key)}
                              icon={<Trash2 size={10} />}
                            >
                              保留「{m.a_title}」· 淘汰「{m.b_title}」
                            </ApplyBtn>
                            <ApplyBtn
                              busy={busyId === key}
                              onClick={() => handleRejectDup(m.b_id, m.a_id, key)}
                              icon={<Trash2 size={10} />}
                            >
                              保留「{m.b_title}」· 淘汰「{m.a_title}」
                            </ApplyBtn>
                          </div>
                          {actionErrors[key] && (
                            <p className="mt-1.5 text-[11px] text-rose-300">
                              操作失败：{actionErrors[key]}
                            </p>
                          )}
                        </>
                      ) : (
                        <p className="mt-1.5 text-[11px] text-emerald-300">
                          ✓ 已处理
                        </p>
                      )}
                    </li>
                  )
                })}
              </Section>

              {/* Stubs */}
              <Section
                title="待充实条目"
                count={result.stubs.length}
                empty="没有发现内容单薄的条目"
              >
                {result.stubs.map(s => {
                  const key = `stub:${s.concept_id}`
                  const v = verdictById.get(s.concept_id)
                  const isDone = done.has(key)
                  return (
                    <li
                      key={key}
                      className="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2"
                    >
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-slate-200">{s.title}</span>
                        <span className="text-[10.5px] text-slate-500">
                          {s.reasons.join(' / ')}
                        </span>
                        {v && (
                          <span className="text-[10px] px-1 py-0 rounded border border-indigo-500/30 bg-indigo-500/10 text-indigo-200">
                            {v.verdict}
                          </span>
                        )}
                      </div>
                      {v?.reason && (
                        <p className="mt-1 text-[11px] text-slate-400">{v.reason}</p>
                      )}
                      {!isDone ? (
                        <div className="mt-1.5 flex flex-wrap gap-2">
                          {s.source_paper_count >= 2 ? (
                            // Multi-source: recompile genuinely re-synthesizes
                            // across the extra papers.
                            <ApplyBtn
                              busy={busyId === key}
                              onClick={() => handleRecompile(s.concept_id)}
                              icon={<Sparkles size={10} />}
                            >
                              重编译此概念页
                            </ApplyBtn>
                          ) : (
                            // Single-source: recompiling is a near no-op
                            // (same lone snippet). Offer the honest actions
                            // instead.
                            <>
                              <ApplyBtn
                                busy={busyId === key}
                                onClick={() => handleRejectStub(s.concept_id)}
                                icon={<Trash2 size={10} />}
                              >
                                淘汰此概念
                              </ApplyBtn>
                              <ApplyBtn
                                busy={busyId === key}
                                onClick={() => handleAcceptStub(s.concept_id)}
                                icon={<Check size={10} />}
                              >
                                标记可接受
                              </ApplyBtn>
                            </>
                          )}
                          {actionErrors[key] && (
                            <p className="basis-full text-[11px] text-rose-300">
                              操作失败：{actionErrors[key]}
                            </p>
                          )}
                        </div>
                      ) : (
                        <p className="mt-1.5 text-[11px] text-emerald-300">
                          ✓ 已处理
                        </p>
                      )}
                    </li>
                  )
                })}
              </Section>

              {/* Missing cross-cut */}
              <Section
                title="建议新建的概念（串联多篇论文）"
                count={
                  (j?.new_concepts || []).length ||
                  result.missing_crosscut.length
                }
                empty="没有发现孤立的论文簇"
              >
                {(j?.new_concepts && j.new_concepts.length > 0
                  ? j.new_concepts.map((n, i) => {
                      const key = `crosscut:${i}`
                      const isDone = done.has(key)
                      return (
                        <li
                          key={`nc-${i}`}
                          className="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2"
                        >
                          <p className="text-slate-200 font-medium">{n.title}</p>
                          <p className="text-[11px] text-slate-400 mt-0.5">
                            {n.rationale}
                          </p>
                          <p className="text-[10.5px] text-slate-600 mt-1">
                            覆盖 {n.paper_ids.length} 篇论文
                          </p>
                          {!isDone ? (
                            <div className="mt-1.5">
                              {onAdoptConcept && (
                                <ApplyBtn
                                  busy={busyId === key}
                                  onClick={() =>
                                    handleAdopt(key, {
                                      title: n.title,
                                      content: n.rationale,
                                      paper_ids: n.paper_ids,
                                    })
                                  }
                                  icon={<Plus size={10} />}
                                >
                                  采纳并新建概念
                                </ApplyBtn>
                              )}
                              {actionErrors[key] && (
                                <p className="mt-1.5 text-[11px] text-rose-300">
                                  操作失败：{actionErrors[key]}
                                </p>
                              )}
                            </div>
                          ) : (
                            <p className="mt-1.5 text-[11px] text-emerald-300">
                              ✓ 已新建概念，将在下次编译概念页时生成 wiki
                            </p>
                          )}
                        </li>
                      )
                    })
                  : result.missing_crosscut.map((c, i) => (
                      <li
                        key={`cc-${i}`}
                        className="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2"
                      >
                        <p className="text-[11px] text-slate-400">
                          {c.size} 篇论文簇未被任何概念串联
                        </p>
                        <p className="text-[10.5px] text-slate-600 mt-1">
                          {c.paper_titles.slice(0, 4).join(' · ')}
                          {c.paper_titles.length > 4 ? ' …' : ''}
                        </p>
                      </li>
                    )))}
              </Section>

              {/* Followups */}
              <Section
                title="建议接着研究的问题"
                count={(j?.followups || []).length}
                empty="本次未生成追问（可勾选 Agent 判定后重跑）"
              >
                {(j?.followups || []).map((q, i) => (
                  <li
                    key={`fu-${i}`}
                    className="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2 flex items-start gap-2"
                  >
                    <span className="flex-1 text-slate-300">{q}</span>
                    <button
                      onClick={() => navigator.clipboard?.writeText(q)}
                      className="shrink-0 inline-flex items-center gap-1 text-[10.5px] text-slate-500 hover:text-slate-200 transition-colors"
                      title="复制问题，丢进 Ask"
                    >
                      <Copy size={10} />
                      复制
                    </button>
                  </li>
                ))}
              </Section>

              {j && !j.used_model && j.error && (
                <div className="px-3 py-2 rounded-lg border border-amber-500/30 bg-amber-500/10 text-amber-200 text-[11px] flex items-center gap-1.5">
                  <AlertTriangle size={11} />
                  Agent 判定未生效（{j.error}）— 仅展示规则层结果
                </div>
              )}
            </>
          )}
        </div>

        <footer className="px-5 py-3 border-t border-slate-800/80 flex items-center gap-3">
          {result && (
            <span className="text-[11px] text-slate-500">
              {done.size > 0
                ? `已处理 ${done.size} 项 · 剩余建议可逐项处理或留待下次`
                : '逐项处理上面的建议，或直接完成关闭'}
            </span>
          )}
          <button
            onClick={onClose}
            className="ml-auto inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-slate-800 hover:bg-slate-700 text-slate-100 text-[12px] font-medium transition-colors"
          >
            <Check size={12} />
            完成
          </button>
        </footer>
      </div>
    </div>
  )
}

function Section({
  title,
  count,
  empty,
  children,
}: {
  title: string
  count: number
  empty: string
  children?: React.ReactNode
}) {
  return (
    <section>
      <h3 className="text-[12px] font-semibold text-white mb-2 flex items-center gap-2">
        {title}
        <span className="text-[10.5px] text-slate-500 tabular-nums">{count}</span>
      </h3>
      {count === 0 ? (
        <p className="text-[11px] text-slate-600">{empty}</p>
      ) : (
        <ul className="space-y-1.5">{children}</ul>
      )}
    </section>
  )
}

function ApplyBtn({
  busy,
  onClick,
  icon,
  children,
}: {
  busy: boolean
  onClick: () => void
  icon: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      disabled={busy}
      className="inline-flex items-center gap-1 text-[10.5px] px-2 py-0.5 rounded border border-slate-700 bg-slate-900 hover:bg-slate-800 text-slate-300 disabled:opacity-50 transition-colors"
    >
      {busy ? <Loader2 size={10} className="animate-spin" /> : icon}
      {children}
    </button>
  )
}
