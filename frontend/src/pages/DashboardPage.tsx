// Data dashboard for the knowledge base project.
//
// Observational only — no action buttons (those live on the [知识] page
// behind the PipelineConsole). The whole page consumes one fat snapshot
// from /api/dashboard/summary so every widget reflects a consistent
// view of the world. Refreshed on demand via the header button; not
// polled, because nothing here changes from one second to the next.

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  BarChart3,
  Loader2,
  RefreshCw,
  TrendingUp,
  Tag as TagIcon,
  Network as NetworkIcon,
  Stethoscope,
  Cpu,
  AlertTriangle,
} from 'lucide-react'
import {
  Radar,
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  CartesianGrid,
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
} from 'recharts'
import {
  getDashboardSummary,
  type DashboardSummary,
  type DashboardCurationCell,
  type DashboardSlice,
} from '../api/client'

// Palette — picked to read on the #0b0d12 canvas.
const SERIES_COLORS = {
  papers: '#6366f1',
  concepts: '#22c55e',
  edges: '#f59e0b',
}
const PIE_PALETTE = [
  '#6366f1', '#22c55e', '#f59e0b', '#ec4899',
  '#14b8a6', '#a855f7', '#ef4444', '#94a3b8',
]

// Shared tooltip styling for every recharts <Tooltip>. Recharts' default
// item text color is black, which is invisible on the dark dashboard
// background — every Tooltip on the page MUST spread these so labels
// + values are readable. See the user-reported bug where pie-slice
// hover showed a tooltip box with no visible content.
const TOOLTIP_CONTENT_STYLE = {
  background: '#0f1117',
  border: '1px solid #334155',
  borderRadius: 6,
  fontSize: 12,
  color: '#cbd5e1',
} as const
const TOOLTIP_ITEM_STYLE = { color: '#e2e8f0' } as const
const TOOLTIP_LABEL_STYLE = { color: '#cbd5e1', fontWeight: 500 } as const

export default function DashboardPage() {
  const [data, setData] = useState<DashboardSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async (isInitial: boolean) => {
    if (isInitial) setLoading(true)
    else setRefreshing(true)
    setError(null)
    try {
      const payload = await getDashboardSummary()
      setData(payload)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => { void load(true) }, [load])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-slate-400 text-sm">
        <Loader2 size={14} className="animate-spin mr-2" /> 加载看板数据…
      </div>
    )
  }
  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-rose-300">
        <AlertTriangle size={28} />
        <div className="text-sm">{error}</div>
        <button
          onClick={() => load(true)}
          className="text-xs px-3 py-1.5 rounded-md border border-slate-700 hover:bg-slate-800"
        >
          重试
        </button>
      </div>
    )
  }
  if (!data) return null

  const generatedAt = new Date(data.generated_at).toLocaleString()

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <header className="bg-[#0f1117] border-b border-slate-800/80 px-6 py-2.5 flex items-center gap-3">
        <BarChart3 size={14} className="text-indigo-300" />
        <h1 className="text-base font-semibold text-white tracking-tight">数据看板</h1>
        <span className="text-xs text-slate-500">·</span>
        <span className="text-xs text-slate-500 tabular-nums">生成于 {generatedAt}</span>
        <button
          onClick={() => load(false)}
          disabled={refreshing}
          className="ml-auto p-2 text-slate-400 hover:text-slate-200 hover:bg-slate-800/60 rounded-xl transition-colors disabled:opacity-50"
          title="刷新数据"
        >
          {refreshing ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
        </button>
      </header>

      {/* Body */}
      <div className="flex-1 overflow-y-auto bg-[#0b0d12] px-6 py-5 space-y-5">
        {/* Row 1: Radar + overview small cards */}
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
          <Card className="xl:col-span-2" title="知识方向（Top-6 标签）" icon={<NetworkIcon size={13} />}>
            {data.radar.length === 0 ? (
              <EmptyState text="尚未生成标签 — 处理一些论文再回来" />
            ) : (
              <RadarPanel radar={data.radar} />
            )}
          </Card>
          <Card title="总览" icon={<TrendingUp size={13} />}>
            <OverviewGrid overview={data.overview} pendingAgeDays={data.pending_age_days} />
          </Card>
        </div>

        {/* Row 2: Growth */}
        <Card title="增长曲线（最近 12 周）" icon={<TrendingUp size={13} />}>
          {data.growth.weeks.length === 0 ? (
            <EmptyState text="暂无时间序列数据" />
          ) : (
            <GrowthPanel growth={data.growth} />
          )}
        </Card>

        {/* Row 3: Distribution + tag cloud */}
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
          <Card title="论文分类" icon={<TagIcon size={13} />}>
            <DistPie data={data.distribution.paper_category} />
          </Card>
          <Card title="节点类型" icon={<TagIcon size={13} />}>
            <DistPie data={data.distribution.node_type} />
          </Card>
          <Card title={`高频标签 Top ${data.top_tags.length}`} icon={<TagIcon size={13} />}>
            <TagCloud tags={data.top_tags} />
          </Card>
        </div>

        {/* Row 4: Curation + network */}
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          <Card title="策展健康" icon={<Stethoscope size={13} />}>
            <CurationPanel
              cells={data.curation}
              pendingAgeDays={data.pending_age_days}
            />
          </Card>
          <Card title={`中枢概念 Top ${data.network.hubs.length}`} icon={<NetworkIcon size={13} />}>
            <HubsPanel network={data.network} />
          </Card>
        </div>

        {/* Row 5: Compile + lint */}
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
          <Card title="论文页编译" icon={<Stethoscope size={13} />}>
            <CompileBucketView bucket={data.compile.papers} />
          </Card>
          <Card title="概念页编译" icon={<Stethoscope size={13} />}>
            <CompileBucketView bucket={data.compile.concepts} />
          </Card>
          <Card title="健检报告" icon={<Stethoscope size={13} />}>
            <LintSummary lint={data.lint} />
          </Card>
        </div>

        {/* Row 6: LLM usage */}
        <Card
          title={`LLM 使用（${data.llm_usage.window_days}d · ${data.llm_usage.total_calls} 次调用 · ${formatTokens(data.llm_usage.total_tokens)} tokens · 成功率 ${Math.round(data.llm_usage.success_rate * 100)}%）`}
          icon={<Cpu size={13} />}
        >
          {data.llm_usage.total_calls === 0 ? (
            <EmptyState text="近 30 天暂无 LLM 调用记录（调用埋点已开启，跑一次处理 / 编译就会有数据）" />
          ) : (
            <LLMUsagePanel usage={data.llm_usage} />
          )}
        </Card>
      </div>
    </div>
  )
}

// ---------- shared shell components ----------

function Card({
  title,
  icon,
  className,
  children,
}: {
  title: string
  icon?: React.ReactNode
  className?: string
  children: React.ReactNode
}) {
  return (
    <section className={`rounded-xl border border-slate-800 bg-slate-900/40 ${className || ''}`}>
      <header className="flex items-center gap-2 px-4 py-2.5 border-b border-slate-800/60">
        {icon && <span className="text-slate-500">{icon}</span>}
        <span className="text-[12.5px] font-semibold text-slate-200 tracking-tight">
          {title}
        </span>
      </header>
      <div className="px-4 py-3">{children}</div>
    </section>
  )
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="py-8 text-center text-[12px] text-slate-500">{text}</div>
  )
}

// ---------- widgets ----------

function OverviewGrid({
  overview,
  pendingAgeDays,
}: {
  overview: DashboardSummary['overview']
  pendingAgeDays: number | null
}) {
  const cards = [
    { label: '论文', value: overview.papers, sub: overview.papers_unprocessed > 0 ? `${overview.papers_unprocessed} 未处理` : undefined },
    { label: '节点', value: overview.nodes, sub: `${overview.concepts_promoted} 已选中` },
    { label: '边', value: overview.edges },
    { label: '标签', value: overview.unique_tags },
    {
      label: '失败',
      value: overview.papers_failed,
      sub: overview.papers_failed > 0 ? '需关注' : undefined,
      tone: overview.papers_failed > 0 ? 'rose' : 'slate',
    },
    {
      label: '待评最久',
      value: pendingAgeDays ?? '—',
      sub: pendingAgeDays != null ? '天' : undefined,
      tone: (pendingAgeDays ?? 0) > 30 ? 'amber' : 'slate',
    },
  ]
  return (
    <div className="grid grid-cols-2 gap-2.5">
      {cards.map((c, i) => (
        <div
          key={i}
          className={`rounded-lg border px-3 py-2 ${
            c.tone === 'rose'
              ? 'border-rose-500/30 bg-rose-500/[0.05]'
              : c.tone === 'amber'
                ? 'border-amber-500/30 bg-amber-500/[0.05]'
                : 'border-slate-800 bg-slate-950/40'
          }`}
        >
          <div className="text-[10.5px] uppercase tracking-wider text-slate-500">
            {c.label}
          </div>
          <div className="text-xl font-semibold text-slate-100 tabular-nums">
            {c.value}
          </div>
          {c.sub && (
            <div className="text-[10.5px] text-slate-500 mt-0.5">{c.sub}</div>
          )}
        </div>
      ))}
    </div>
  )
}

function RadarPanel({ radar }: { radar: DashboardSummary['radar'] }) {
  // Recharts radar needs one row per axis with each series as a key.
  // Series live on different scales (papers count vs edge_density 0-1)
  // so we normalize to a "fraction of max in this series" so all three
  // share the same 0-1 axis. We expose raw values via tooltip.
  const data = useMemo(() => {
    const maxPapers = Math.max(1, ...radar.map(r => r.papers))
    const maxConcepts = Math.max(1, ...radar.map(r => r.concepts))
    const maxDensity = Math.max(0.001, ...radar.map(r => r.edge_density))
    return radar.map(r => ({
      tag: r.tag,
      papers: r.papers / maxPapers,
      concepts: r.concepts / maxConcepts,
      edges: r.edge_density / maxDensity,
      // raw values retained for tooltip
      raw_papers: r.papers,
      raw_concepts: r.concepts,
      raw_edges: r.edge_density.toFixed(3),
    }))
  }, [radar])
  return (
    <div className="w-full h-[320px]">
      <ResponsiveContainer width="100%" height="100%">
        <RadarChart data={data}>
          <PolarGrid stroke="#334155" />
          <PolarAngleAxis
            dataKey="tag"
            tick={{ fill: '#94a3b8', fontSize: 11 }}
          />
          <PolarRadiusAxis
            angle={90}
            domain={[0, 1]}
            tick={{ fill: '#475569', fontSize: 9 }}
            tickFormatter={() => ''}
          />
          <Radar
            name="论文"
            dataKey="papers"
            stroke={SERIES_COLORS.papers}
            fill={SERIES_COLORS.papers}
            fillOpacity={0.25}
          />
          <Radar
            name="概念"
            dataKey="concepts"
            stroke={SERIES_COLORS.concepts}
            fill={SERIES_COLORS.concepts}
            fillOpacity={0.18}
          />
          <Radar
            name="边密度"
            dataKey="edges"
            stroke={SERIES_COLORS.edges}
            fill={SERIES_COLORS.edges}
            fillOpacity={0.15}
          />
          <Tooltip
            contentStyle={TOOLTIP_CONTENT_STYLE}
            itemStyle={TOOLTIP_ITEM_STYLE}
            labelStyle={TOOLTIP_LABEL_STYLE}
            formatter={((value: unknown, name: unknown, item: unknown) => {
              // Recharts gives us the normalized value (0-1) per series;
              // show the raw counts/density in the tooltip instead.
              const row =
                (item as { payload?: Record<string, unknown> } | undefined)
                  ?.payload
              if (!row) return [value as React.ReactNode, name as string]
              if (name === '论文') return [row.raw_papers as React.ReactNode, name as string]
              if (name === '概念') return [row.raw_concepts as React.ReactNode, name as string]
              if (name === '边密度') return [row.raw_edges as React.ReactNode, name as string]
              return [value as React.ReactNode, name as string]
            }) as never}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  )
}

function GrowthPanel({ growth }: { growth: DashboardSummary['growth'] }) {
  const data = growth.weeks.map((week, i) => ({
    week: week.slice(5),
    papers: growth.papers[i] ?? 0,
    concepts: growth.concepts[i] ?? 0,
    edges: growth.edges[i] ?? 0,
  }))
  return (
    <div className="w-full h-[240px]">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 5, right: 15, left: -10, bottom: 5 }}>
          <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
          <XAxis dataKey="week" tick={{ fill: '#94a3b8', fontSize: 11 }} />
          <YAxis tick={{ fill: '#94a3b8', fontSize: 11 }} />
          <Tooltip
            contentStyle={TOOLTIP_CONTENT_STYLE}
            itemStyle={TOOLTIP_ITEM_STYLE}
            labelStyle={TOOLTIP_LABEL_STYLE}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Line
            type="monotone"
            dataKey="papers"
            name="论文"
            stroke={SERIES_COLORS.papers}
            strokeWidth={2}
            dot={false}
          />
          <Line
            type="monotone"
            dataKey="concepts"
            name="概念"
            stroke={SERIES_COLORS.concepts}
            strokeWidth={2}
            dot={false}
          />
          <Line
            type="monotone"
            dataKey="edges"
            name="边"
            stroke={SERIES_COLORS.edges}
            strokeWidth={2}
            dot={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

function DistPie({ data }: { data: DashboardSlice[] }) {
  if (data.length === 0) return <EmptyState text="暂无数据" />
  // Pre-compute total so the legend can show percentages. The recharts
  // tooltip already shows raw values, so percentages here are the
  // additive context the user actually misses.
  const total = data.reduce((s, d) => s + d.value, 0) || 1
  return (
    <div className="w-full">
      <div className="w-full h-[200px]">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              nameKey="label"
              cx="50%"
              cy="50%"
              outerRadius="80%"
              label={{ fill: '#cbd5e1', fontSize: 11 }}
            >
              {data.map((_, i) => (
                <Cell key={i} fill={PIE_PALETTE[i % PIE_PALETTE.length]} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={TOOLTIP_CONTENT_STYLE}
              itemStyle={TOOLTIP_ITEM_STYLE}
              labelStyle={TOOLTIP_LABEL_STYLE}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
      {/* Color → category legend. Recharts' built-in <Legend> overlaps
          the chart at small heights, so we render bordered chips
          beneath the pie instead. Same `i % palette.length` indexing
          as the Cells above keeps colors in sync. Chip styling matches
          the existing CompileBucketView so the dashboard's color
          vocabulary stays consistent. */}
      <div className="mt-3 flex flex-wrap gap-1.5">
        {data.map((slice, i) => {
          const pct = Math.round((slice.value / total) * 100)
          return (
            <div
              key={`${slice.label}-${i}`}
              className="inline-flex items-baseline gap-1.5 rounded-md border border-slate-800 bg-slate-950/40 px-2 py-1"
              title={`${slice.label} · ${slice.value} · ${pct}%`}
            >
              <span
                aria-hidden
                className="h-2 w-2 shrink-0 self-center rounded-full"
                style={{ background: PIE_PALETTE[i % PIE_PALETTE.length] }}
              />
              <span className="max-w-[10rem] truncate text-[11.5px] text-slate-200">
                {slice.label}
              </span>
              <span className="text-[10.5px] text-slate-500 tabular-nums">
                {slice.value}·{pct}%
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function TagCloud({ tags }: { tags: DashboardSlice[] }) {
  if (tags.length === 0) return <EmptyState text="暂无标签" />
  const max = Math.max(...tags.map(t => t.value))
  return (
    <div className="flex flex-wrap gap-1.5 items-baseline">
      {tags.map(t => {
        const weight = t.value / max
        const fontSize = 11 + Math.round(weight * 6) // 11–17px
        const opacity = 0.55 + weight * 0.45
        return (
          <span
            key={t.label}
            className="inline-flex items-baseline gap-1 px-2 py-0.5 rounded-md bg-slate-800/40 border border-slate-700/60"
            style={{ fontSize, opacity }}
            title={`${t.value} 次`}
          >
            <span className="text-slate-200">{t.label}</span>
            <span className="text-slate-500 text-[10px] tabular-nums">{t.value}</span>
          </span>
        )
      })}
    </div>
  )
}

function CurationPanel({
  cells,
  pendingAgeDays,
}: {
  cells: DashboardCurationCell[]
  pendingAgeDays: number | null
}) {
  // Pivot the (status, by) cells into a stacked-bar payload.
  const statuses: Array<'pending' | 'promoted' | 'rejected'> = [
    'promoted',
    'pending',
    'rejected',
  ]
  const byKeys = Array.from(new Set(cells.map(c => c.by)))
  const data = statuses.map(s => {
    const row: Record<string, number | string> = { status: s }
    let total = 0
    for (const by of byKeys) {
      const found = cells.find(c => c.status === s && c.by === by)
      const v = found?.count ?? 0
      row[by] = v
      total += v
    }
    row['__total'] = total
    return row
  })
  return (
    <div>
      <div className="w-full h-[200px]">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ left: 10, right: 20 }}>
            <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" horizontal={false} />
            <XAxis type="number" tick={{ fill: '#94a3b8', fontSize: 11 }} />
            <YAxis
              type="category"
              dataKey="status"
              tick={{ fill: '#cbd5e1', fontSize: 11 }}
              tickFormatter={(v) =>
                ({ promoted: '已选中', pending: '待评', rejected: '淘汰' })[v as string] || v
              }
            />
            <Tooltip
              contentStyle={TOOLTIP_CONTENT_STYLE}
              itemStyle={TOOLTIP_ITEM_STYLE}
              labelStyle={TOOLTIP_LABEL_STYLE}
            />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            {byKeys.map((by, i) => (
              <Bar
                key={by}
                dataKey={by}
                stackId="curation"
                fill={PIE_PALETTE[i % PIE_PALETTE.length]}
                name={
                  { user: 'human', llm: 'agent', heuristic: 'heuristic', legacy: 'legacy', unset: '未决' }[
                    by
                  ] || by
                }
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
      {pendingAgeDays != null && (
        <div className="mt-2 text-[11px] text-slate-500">
          最早的待评候选已积压 <span className="text-amber-300 tabular-nums">{pendingAgeDays}</span> 天
        </div>
      )}
    </div>
  )
}

function HubsPanel({ network }: { network: DashboardSummary['network'] }) {
  if (network.hubs.length === 0) {
    return <EmptyState text="暂无可统计的中枢概念" />
  }
  const max = Math.max(...network.hubs.map(h => h.degree))
  return (
    <div className="space-y-1.5">
      {network.hubs.map(h => (
        <div key={h.id} className="flex items-center gap-2 text-[12px]">
          <div className="w-40 truncate text-slate-200" title={h.title}>{h.title}</div>
          <div className="flex-1 h-2 rounded bg-slate-800/60 overflow-hidden">
            <div
              className="h-full bg-indigo-400"
              style={{ width: `${Math.max(4, Math.round((h.degree / max) * 100))}%` }}
            />
          </div>
          <div className="text-[11px] text-slate-400 tabular-nums w-10 text-right">
            {h.degree}
          </div>
        </div>
      ))}
      <div className="mt-3 flex items-center gap-4 text-[11px] text-slate-500">
        <span>孤儿节点：<span className="text-slate-300 tabular-nums">{network.orphan_count}</span></span>
        <span>平均度：<span className="text-slate-300 tabular-nums">{network.avg_degree}</span></span>
        <span className="ml-auto">
          关系：
          {network.relation_types.slice(0, 3).map(r => (
            <span key={r.label} className="ml-2">
              {r.label} <span className="text-slate-400 tabular-nums">{r.value}</span>
            </span>
          ))}
        </span>
      </div>
    </div>
  )
}

function CompileBucketView({
  bucket,
}: {
  bucket: DashboardSummary['compile']['papers']
}) {
  const total = bucket.total || 1
  const segments = [
    { label: '就绪', value: bucket.ok, color: '#22c55e' },
    { label: '待编译', value: bucket.missing, color: '#94a3b8' },
    { label: '已过期', value: bucket.stale, color: '#f59e0b' },
    { label: '孤儿', value: bucket.orphan, color: '#94a3b8' },
  ]
  return (
    <div>
      <div className="flex h-3 overflow-hidden rounded-md border border-slate-800">
        {segments.map(s => (
          <div
            key={s.label}
            title={`${s.label} ${s.value}`}
            style={{
              width: `${(s.value / total) * 100}%`,
              background: s.color,
              opacity: s.value === 0 ? 0 : 1,
            }}
          />
        ))}
      </div>
      <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11.5px]">
        {segments.map(s => (
          <div key={s.label} className="flex items-baseline gap-1.5">
            <span
              className="inline-block w-2 h-2 rounded-full"
              style={{ background: s.color }}
            />
            <span className="text-slate-400">{s.label}</span>
            <span className="text-slate-200 tabular-nums">{s.value}</span>
          </div>
        ))}
      </div>
      <div className="mt-2 text-[11px] text-slate-500">
        合计 <span className="text-slate-200 tabular-nums">{bucket.total}</span>
      </div>
    </div>
  )
}

function LintSummary({ lint }: { lint: DashboardSummary['lint'] }) {
  if (!lint.exists) {
    return <EmptyState text="尚未生成健检报告" />
  }
  const counts = lint.counts || { stubs: 0, merges: 0, missing_crosscut: 0, followups: 0 }
  const items = [
    { label: '短桩', value: counts.stubs, tone: 'amber' as const },
    { label: '可合并', value: counts.merges, tone: 'amber' as const },
    { label: '待建概念', value: counts.missing_crosscut, tone: 'amber' as const },
    { label: '追问', value: counts.followups, tone: 'slate' as const },
  ]
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-2">
        {items.map(i => (
          <div
            key={i.label}
            className={`rounded-lg border px-3 py-2 ${
              i.tone === 'amber' && i.value > 0
                ? 'border-amber-500/30 bg-amber-500/[0.05]'
                : 'border-slate-800 bg-slate-950/40'
            }`}
          >
            <div className="text-[10.5px] uppercase tracking-wider text-slate-500">
              {i.label}
            </div>
            <div className="text-xl font-semibold text-slate-100 tabular-nums">
              {i.value}
            </div>
          </div>
        ))}
      </div>
      {lint.modified_at && (
        <div className="text-[11px] text-slate-500">
          报告更新于 {new Date(lint.modified_at).toLocaleString()}
        </div>
      )}
    </div>
  )
}

function LLMUsagePanel({ usage }: { usage: DashboardSummary['llm_usage'] }) {
  // by-task: horizontal bar of token totals.
  const taskData = usage.by_task.slice(0, 8).map(t => ({
    task: t.task,
    tokens: t.total_tokens,
    calls: t.calls,
    avg_latency_ms: t.avg_latency_ms ?? 0,
  }))
  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <div>
        <div className="text-[10.5px] uppercase tracking-wider text-slate-500 mb-1.5">
          按任务（按 token 总量排序）
        </div>
        <div className="w-full h-[200px]">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={taskData} layout="vertical" margin={{ left: 10, right: 20 }}>
              <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" tick={{ fill: '#94a3b8', fontSize: 11 }} tickFormatter={formatTokens} />
              <YAxis type="category" dataKey="task" tick={{ fill: '#cbd5e1', fontSize: 11 }} width={100} />
              <Tooltip
                contentStyle={TOOLTIP_CONTENT_STYLE}
                itemStyle={TOOLTIP_ITEM_STYLE}
                labelStyle={TOOLTIP_LABEL_STYLE}
                formatter={(value, name, payload) => {
                  if (name === 'tokens') {
                    const row = payload?.payload as Record<string, unknown> | undefined
                    return [
                      `${formatTokens(Number(value))} · ${row?.calls} 调用 · 平均 ${row?.avg_latency_ms}ms`,
                      'tokens',
                    ]
                  }
                  return [value, name]
                }}
              />
              <Bar dataKey="tokens" fill={SERIES_COLORS.papers} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div>
        <div className="text-[10.5px] uppercase tracking-wider text-slate-500 mb-1.5">
          按模型
        </div>
        <div className="space-y-1.5 overflow-y-auto max-h-[200px] pr-1">
          {usage.by_model.map((m, i) => (
            <div
              key={`${m.model}:${m.provider}:${i}`}
              className="flex items-center gap-2 text-[12px] border-b border-slate-800/40 pb-1.5 last:border-0"
            >
              <div className="flex-1 truncate">
                <span className="text-slate-200">{m.model}</span>
                <span className="text-slate-500 ml-2 text-[10.5px]">{m.provider}</span>
              </div>
              <span className="text-slate-300 tabular-nums w-16 text-right">{m.calls}</span>
              <span className="text-slate-400 tabular-nums w-20 text-right">{formatTokens(m.total_tokens)}</span>
              <span className="text-slate-500 tabular-nums w-12 text-right">
                {m.avg_latency_ms != null ? `${m.avg_latency_ms}ms` : '—'}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function formatTokens(n: number): string {
  if (n < 1000) return String(n)
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`
  return `${(n / 1_000_000).toFixed(2)}M`
}
