import { useEffect, useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  X,
  FileText,
  Tag,
  Link as LinkIcon,
  Clock,
  Pencil,
  Check,
  RotateCcw,
  Loader2,
  Sparkles,
  BookOpen,
  Info,
  Copy,
} from 'lucide-react'
import {
  getNode,
  firstPageUrl,
  updatePromotionStatus,
  listPaperPages,
  listConceptPages,
  getPaperPage,
  getConceptPage,
  recompilePaper,
  recompileConcept,
  type NodeDetail as NodeDetailType,
  type GraphNode,
  type PromotionStatus,
  type WikiPageDetail,
} from '../api/client'

const TYPE_STYLES: Record<string, { bg: string; text: string; label: string }> = {
  paper: { bg: 'bg-indigo-500/15', text: 'text-indigo-300', label: '论文' },
  technique: { bg: 'bg-emerald-500/15', text: 'text-emerald-300', label: '技术' },
  dataset: { bg: 'bg-amber-500/15', text: 'text-amber-300', label: '数据集' },
  problem_area: { bg: 'bg-cyan-500/15', text: 'text-cyan-300', label: '研究领域' },
  concept: { bg: 'bg-teal-500/15', text: 'text-teal-300', label: '概念' },
  entity: { bg: 'bg-pink-500/15', text: 'text-pink-300', label: '实体' },
  topic: { bg: 'bg-indigo-500/15', text: 'text-indigo-300', label: '主题' },
  fact: { bg: 'bg-amber-500/15', text: 'text-amber-300', label: '事实' },
}

const RELATION_LABELS: Record<string, string> = {
  uses: '使用',
  builds_on: '基于',
  trained_on: '训练于',
  evaluated_on: '评测于',
  compared_to: '对比',
  similar: '相似',
  related: '相关',
  contrasts_with: '对照',
  belongs_to: '属于',
  curated_link: '人工关联',
}

interface Props {
  node: GraphNode | null
  onClose: () => void
  onNavigate: (nodeId: string) => void
  onEditManualConcept: (node: GraphNode) => void
  onPromotionChanged?: (nodeId: string, status: PromotionStatus) => void
  busyNodeId?: string | null
  // Optional preferred tab when the drawer (re)opens for a new node — set
  // by the wiki search dropdown so a wiki-hit click lands the user
  // straight on the rendered .md instead of the default detail view.
  initialTab?: 'detail' | 'wiki'
}

const PROMOTION_BADGE: Record<PromotionStatus, { bg: string; text: string; label: string }> = {
  pending: { bg: 'bg-amber-500/15 border border-amber-500/40', text: 'text-amber-200', label: '待评审' },
  promoted: { bg: 'bg-emerald-500/15 border border-emerald-500/40', text: 'text-emerald-200', label: '已精选' },
  rejected: { bg: 'bg-rose-500/15 border border-rose-500/40', text: 'text-rose-200', label: '已淘汰' },
}

export default function NodeDetail({
  node,
  onClose,
  onNavigate,
  onEditManualConcept,
  onPromotionChanged,
  busyNodeId,
  initialTab,
}: Props) {
  const [detail, setDetail] = useState<NodeDetailType | null>(null)
  const [promotionBusy, setPromotionBusy] = useState(false)
  // The Cytoscape-synced status can lag a tick; once the user changes
  // status from the drawer we apply it locally so the badge / button row
  // reflect the new state without a roundtrip-and-rerender.
  const [localStatus, setLocalStatus] = useState<PromotionStatus | null>(null)
  const [localPromotedBy, setLocalPromotedBy] = useState<string | null>(null)
  const [localReason, setLocalReason] = useState<string | null>(null)
  // Two-tab drawer: 详情 (current rich panel) and Wiki (rendered .md). The
  // Wiki tab only fetches when activated to avoid wasted listing on every
  // node click.
  const [tab, setTab] = useState<'detail' | 'wiki'>('detail')
  const [wiki, setWiki] = useState<WikiPageDetail | null>(null)
  const [wikiLoading, setWikiLoading] = useState(false)
  const [wikiError, setWikiError] = useState<string | null>(null)
  const [wikiMissing, setWikiMissing] = useState(false)
  const [wikiBusy, setWikiBusy] = useState(false)
  const nodeId = node?.id

  useEffect(() => {
    if (!nodeId) return

    let cancelled = false
    const loadDetail = async () => {
      try {
        const result = await getNode(parseInt(nodeId))
        if (!cancelled) setDetail(result)
      } catch (error) {
        console.error('Failed to load node detail', error)
        if (!cancelled) setDetail(null)
      }
    }

    setLocalStatus(null)
    setLocalPromotedBy(null)
    setLocalReason(null)
    setTab(initialTab ?? 'detail')
    setWiki(null)
    setWikiError(null)
    setWikiMissing(false)
    void loadDetail()
    return () => { cancelled = true }
  }, [nodeId, initialTab])

  const status: PromotionStatus = localStatus || node?.promotion_status || 'promoted'
  // Wiki tab is meaningful when the node has a corresponding .md file:
  //   - paper nodes always
  //   - concept-candidate nodes only when promoted (otherwise nothing was
  //     compiled in the first place)
  const wikiKind: 'papers' | 'concepts' | null = useMemo(() => {
    if (!node) return null
    if (node.node_type === 'paper') return 'papers'
    if (node.concept_candidate && status === 'promoted') return 'concepts'
    return null
  }, [node, status])

  // Lazy-load the .md content when Wiki tab is opened. We resolve the
  // filename via the per-kind list (paper_id / concept_id → filename).
  // This is one extra GET on first open, but the result is fine to leave
  // cached because we re-resolve when the node id changes.
  useEffect(() => {
    if (!node || tab !== 'wiki' || !wikiKind) return
    if (wiki && wiki.path && (
      (wikiKind === 'papers' && wiki.paper_id === Number(node.id)) ||
      (wikiKind === 'concepts' && wiki.concept_id === Number(node.id))
    )) return

    let cancelled = false
    const load = async () => {
      setWikiLoading(true)
      setWikiError(null)
      setWikiMissing(false)
      try {
        const targetId = Number(node.id)
        const items = wikiKind === 'papers' ? await listPaperPages() : await listConceptPages()
        const match = items.find(it =>
          wikiKind === 'papers' ? it.paper_id === targetId : it.concept_id === targetId,
        )
        if (cancelled) return
        if (!match) {
          setWiki(null)
          setWikiMissing(true)
          return
        }
        const detail = wikiKind === 'papers'
          ? await getPaperPage(match.filename)
          : await getConceptPage(match.filename)
        if (!cancelled) setWiki(detail)
      } catch (e) {
        if (!cancelled) setWikiError(e instanceof Error ? e.message : String(e))
      } finally {
        if (!cancelled) setWikiLoading(false)
      }
    }
    void load()
    return () => { cancelled = true }
  }, [node, tab, wikiKind, wiki])

  if (!node) return null

  const visibleDetail = String(detail?.id) === node.id ? detail : null
  const style = TYPE_STYLES[node.node_type] || { bg: 'bg-slate-700', text: 'text-slate-300', label: node.node_type }

  const promotedBy = localPromotedBy ?? node.promoted_by
  const promotionReason = localReason ?? node.promotion_reason
  const showPromotion = node.concept_candidate

  const handleRecompileWiki = async () => {
    if (!wikiKind || !node) return
    setWikiBusy(true)
    setWikiError(null)
    try {
      const targetId = Number(node.id)
      const result = wikiKind === 'papers'
        ? await recompilePaper(targetId)
        : await recompileConcept(targetId)
      // Re-fetch the freshly compiled markdown immediately.
      const detail = wikiKind === 'papers'
        ? await getPaperPage(result.filename)
        : await getConceptPage(result.filename)
      setWiki(detail)
      setWikiMissing(false)
    } catch (e) {
      setWikiError(e instanceof Error ? e.message : String(e))
    } finally {
      setWikiBusy(false)
    }
  }

  const handlePromotionAction = async (next: PromotionStatus) => {
    if (!node) return
    setPromotionBusy(true)
    try {
      const result = await updatePromotionStatus(parseInt(node.id), next)
      setLocalStatus(result.node.promotion_status)
      setLocalPromotedBy(result.node.promoted_by)
      setLocalReason(result.node.promotion_reason)
      onPromotionChanged?.(node.id, result.node.promotion_status)
    } catch (e) {
      console.error('Failed to update promotion status', e)
    } finally {
      setPromotionBusy(false)
    }
  }

  return (
    <aside className="w-[26rem] max-w-[42vw] min-w-[22rem] h-full bg-[#0f1117] border-l border-slate-800/80 flex flex-col overflow-hidden fade-in shrink-0">
      {/* Header */}
      <div className="px-5 py-4 border-b border-slate-800/80">
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex flex-wrap items-center gap-2 mb-2">
              <span className={`chip ${style.bg} ${style.text} text-xs`}>
                {style.label}
              </span>
              <span className={`chip text-xs ${node.origin === 'manual' ? 'bg-teal-500/10 text-teal-200 border border-teal-500/30' : 'bg-slate-800 text-slate-400 border border-slate-700/60'}`}>
                {node.origin === 'manual' ? '手动新增' : '自动抽取'}
              </span>
              {visibleDetail?.connected_nodes && (
                <span className="text-xs text-slate-500">
                  {visibleDetail.connected_nodes.length} 个关联
                </span>
              )}
            </div>
            <h3 className="text-lg font-semibold text-white leading-snug tracking-tight text-safe-wrap">
              {node.title}
            </h3>
            <p className="text-sm text-slate-500 mt-2">
              查看节点简介、来源论文与关联关系。
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-slate-500 hover:text-white p-1.5 -mr-1 shrink-0 rounded-lg hover:bg-slate-800/60 transition-colors"
          >
            <X size={16} />
          </button>
        </div>
      </div>

      {/* Tabs — only show when this node has a wiki page available */}
      {wikiKind && (
        <div className="px-5 pt-3 flex gap-1 border-b border-slate-800/80">
          <DrawerTab
            active={tab === 'detail'}
            onClick={() => setTab('detail')}
            icon={<Info size={12} />}
          >
            详情
          </DrawerTab>
          <DrawerTab
            active={tab === 'wiki'}
            onClick={() => setTab('wiki')}
            icon={<BookOpen size={12} />}
          >
            Wiki
          </DrawerTab>
        </div>
      )}

      {/* Body */}
      {tab === 'wiki' && wikiKind ? (
        <WikiTabBody
          kind={wikiKind}
          wiki={wiki}
          loading={wikiLoading}
          error={wikiError}
          missing={wikiMissing}
          busy={wikiBusy}
          onRecompile={handleRecompileWiki}
        />
      ) : (
      <div className="flex-1 overflow-y-auto p-5 space-y-6">
        {/* Content */}
        {node.content && (
          <section className="surface-card p-4">
            <div className="section-label mb-2">节点概述</div>
            <p className="prose-reading whitespace-pre-wrap text-safe-wrap">{node.content}</p>
          </section>
        )}

        {showPromotion && (
          <section className="surface-card p-4">
            <div className="flex items-center gap-2 mb-3">
              <Sparkles size={12} className="text-indigo-300" />
              <span className="section-label">概念精选</span>
              <span className={`chip ml-auto ${PROMOTION_BADGE[status].bg} ${PROMOTION_BADGE[status].text} text-[11px]`}>
                {PROMOTION_BADGE[status].label}
              </span>
            </div>
            {promotedBy && (
              <p className="text-[11px] text-slate-500 mb-2">
                由 <span className="text-slate-400">{promotedBy}</span> 决定
                {promotionReason ? ` · ${promotionReason}` : ''}
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              {status !== 'promoted' && (
                <PromotionButton
                  busy={promotionBusy}
                  tone="emerald"
                  icon={<Check size={13} />}
                  onClick={() => handlePromotionAction('promoted')}
                >
                  精选
                </PromotionButton>
              )}
              {status !== 'rejected' && (
                <PromotionButton
                  busy={promotionBusy}
                  tone="rose"
                  icon={<X size={13} />}
                  onClick={() => handlePromotionAction('rejected')}
                >
                  淘汰
                </PromotionButton>
              )}
              {status !== 'pending' && (
                <PromotionButton
                  busy={promotionBusy}
                  tone="slate"
                  icon={<RotateCcw size={13} />}
                  onClick={() => handlePromotionAction('pending')}
                >
                  重置评审
                </PromotionButton>
              )}
            </div>
            <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
              你的决定会标记为 <span className="text-slate-400">user</span>，下次自动评审不会再覆盖它。
            </p>
          </section>
        )}

        {visibleDetail?.can_edit && (
          <section className="surface-card p-4">
            <div className="section-label mb-3">节点操作</div>
            <button
              onClick={() => onEditManualConcept(node)}
              disabled={busyNodeId === node.id}
              className="inline-flex items-center gap-1.5 rounded-xl border border-slate-700 bg-slate-900/70 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800 disabled:opacity-50"
            >
              <Pencil size={13} />
              编辑概念
            </button>
            <p className="mt-3 text-xs leading-relaxed text-slate-500">
              要把这个概念从图谱里移除，请用上面的「淘汰」 —— 它会进入精选生命周期，可在「查看 / 召回 已淘汰」里召回。
            </p>
          </section>
        )}

        {/* Created at */}
        {node.created_at && (
          <p className="text-xs text-slate-500 flex items-center gap-1.5">
            <Clock size={11} />
            创建于 {new Date(node.created_at).toLocaleString()}
          </p>
        )}

        {/* Aliases / tags */}
        {node.tags && node.tags.length > 0 && (
          <section>
            <div className="flex items-center gap-1.5 mb-2.5 text-slate-500">
              <Tag size={11} />
              <span className="section-label">别名 / 标签</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {node.tags.map(tag => (
                <span
                  key={tag}
                  className="chip bg-slate-800/80 text-slate-300 border border-slate-700/40 text-xs"
                >
                  {tag}
                </span>
              ))}
            </div>
          </section>
        )}

        {/* Source papers */}
        {node.source_paper_ids && node.source_paper_ids.length > 0 && (
          <section>
            <div className="flex items-center gap-1.5 mb-2.5 text-slate-500">
              <FileText size={11} />
              <span className="section-label">来源论文 · {node.source_paper_ids.length}</span>
            </div>
            {visibleDetail?.linked_papers && visibleDetail.linked_papers.length > 0 && (
              <div className="space-y-1.5 mb-3">
                {visibleDetail.linked_papers.map(paper => (
                  <div
                    key={paper.id}
                    className="rounded-xl border border-slate-800 bg-slate-900/60 px-3 py-2"
                  >
                    <p className="text-sm text-slate-200 leading-snug text-safe-wrap">{paper.title}</p>
                    <p className="mt-1 text-[11px] text-slate-500">
                      paper #{paper.id} · {paper.processed ? '已处理' : '未处理'}
                    </p>
                  </div>
                ))}
              </div>
            )}
            <div className="grid grid-cols-2 gap-2.5">
              {node.source_paper_ids.slice(0, 9).map(pid => (
                <img
                  key={pid}
                  src={firstPageUrl(pid)}
                  alt={`paper-${pid}`}
                  className="w-full aspect-[3/4] object-cover rounded-lg border border-slate-800 hover:border-indigo-500/60 transition-colors"
                  onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
                />
              ))}
            </div>
          </section>
        )}

        {/* Connected nodes */}
        {visibleDetail?.connected_nodes && visibleDetail.connected_nodes.length > 0 && (
          <section>
            <div className="flex items-center gap-1.5 mb-2.5 text-slate-500">
              <LinkIcon size={11} />
              <span className="section-label">关联节点 · {visibleDetail.connected_nodes.length}</span>
            </div>
            <div className="space-y-1.5">
              {visibleDetail.connected_nodes.slice(0, 20).map(cn => {
                const edge = visibleDetail.edges.find(
                  e => e.source === cn.id || e.target === cn.id
                )
                const rel = edge?.relation_type
                const cnStyle = TYPE_STYLES[cn.node_type] || { bg: 'bg-slate-700', text: 'text-slate-400', label: cn.node_type }
                return (
                  <button
                    key={cn.id}
                    onClick={() => onNavigate(String(cn.id))}
                    className="w-full text-left px-3.5 py-3 bg-slate-900/60 hover:bg-slate-800 border border-slate-800 hover:border-slate-700 rounded-xl transition-colors group"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm text-slate-200 group-hover:text-white leading-snug text-safe-wrap">
                        {cn.title}
                      </span>
                      <span className={`chip ${cnStyle.bg} ${cnStyle.text} text-[10px] shrink-0`}>
                        {cnStyle.label}
                      </span>
                    </div>
                    {rel && rel !== 'similar' && (
                      <p className="text-xs text-slate-500 mt-1.5">
                        {RELATION_LABELS[rel] || rel}
                      </p>
                    )}
                  </button>
                )
              })}
            </div>
          </section>
        )}
      </div>
      )}
    </aside>
  )
}

function DrawerTab({
  active,
  onClick,
  icon,
  children,
}: {
  active: boolean
  onClick: () => void
  icon: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-2 text-[12px] inline-flex items-center gap-1.5 border-b-2 -mb-px transition-colors ${
        active
          ? 'text-white border-indigo-400'
          : 'text-slate-500 border-transparent hover:text-slate-200'
      }`}
    >
      {icon}
      {children}
    </button>
  )
}

function WikiTabBody({
  kind,
  wiki,
  loading,
  error,
  missing,
  busy,
  onRecompile,
}: {
  kind: 'papers' | 'concepts'
  wiki: WikiPageDetail | null
  loading: boolean
  error: string | null
  missing: boolean
  busy: boolean
  onRecompile: () => void
}) {
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="px-5 py-3 border-b border-slate-800/80 flex items-center gap-2 flex-wrap text-[11px] text-slate-500">
        {wiki ? (
          <>
            <code className="font-mono text-slate-300 bg-slate-900/60 border border-slate-800 rounded px-1.5 py-0.5 break-all">
              {wiki.path}
            </code>
            <button
              onClick={() => navigator.clipboard?.writeText(wiki.path)}
              className="text-slate-500 hover:text-slate-200 transition-colors inline-flex items-center gap-1"
              title="复制项目相对路径"
            >
              <Copy size={10} /> 复制
            </button>
            <span className="ml-auto text-slate-600">
              {wiki.compiled_at ? new Date(wiki.compiled_at).toLocaleString() : ''}
            </span>
          </>
        ) : (
          <span className="text-slate-500">{kind === 'papers' ? '论文页' : '概念页'}</span>
        )}
        <button
          onClick={onRecompile}
          disabled={busy}
          className="ml-auto inline-flex items-center gap-1 text-[11px] bg-indigo-500/15 hover:bg-indigo-500/25 text-indigo-200 border border-indigo-500/40 px-2 py-0.5 rounded-md disabled:opacity-50 transition-colors"
        >
          {busy ? <Loader2 size={11} className="animate-spin" /> : <Sparkles size={11} />}
          重编译此页
        </button>
      </div>

      <div className="px-5 py-4">
        {loading ? (
          <div className="py-12 flex items-center justify-center text-slate-500 text-[12px]">
            <Loader2 size={12} className="animate-spin mr-2" /> 加载中…
          </div>
        ) : error ? (
          <div className="px-3 py-2 rounded-lg border border-rose-500/40 bg-rose-500/10 text-rose-200 text-[12px]">
            {error}
          </div>
        ) : missing || !wiki ? (
          <div className="px-3 py-3 rounded-lg border border-amber-500/30 bg-amber-500/10 text-[12px] text-amber-100 leading-relaxed">
            <p>此节点尚未生成 wiki .md 文件。</p>
            <p className="mt-1 text-amber-200/70 text-[11px]">
              点上方"重编译此页"会调用 LLM 生成。
            </p>
          </div>
        ) : (
          <div className="markdown-notes max-w-none text-[13px] leading-7 text-slate-200">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{wiki.body}</ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  )
}

function PromotionButton({
  busy,
  tone,
  icon,
  onClick,
  children,
}: {
  busy: boolean
  tone: 'emerald' | 'rose' | 'slate'
  icon: React.ReactNode
  onClick: () => void
  children: React.ReactNode
}) {
  const palette = {
    emerald: 'bg-emerald-500/15 hover:bg-emerald-500/25 text-emerald-200 border-emerald-500/40',
    rose: 'bg-rose-500/15 hover:bg-rose-500/25 text-rose-200 border-rose-500/40',
    slate: 'bg-slate-800 hover:bg-slate-700 text-slate-300 border-slate-700',
  }[tone]
  return (
    <button
      onClick={onClick}
      disabled={busy}
      className={`inline-flex items-center gap-1.5 text-[12px] px-2.5 py-1.5 rounded-lg border transition-colors disabled:opacity-50 ${palette}`}
    >
      {busy ? <Loader2 size={12} className="animate-spin" /> : icon}
      {children}
    </button>
  )
}
