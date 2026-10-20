import { useRef } from 'react'
import type { DomainAnalysis, DomainEvidence, DomainPaper } from '../api/domains'
import { t as tr } from '../i18n/catalog'
import { sourceUrl } from '../utils/sourceUrl'

const aspectLabel = (aspect: 'purpose' | 'assessment' | 'comparison') =>
  tr(aspect === 'purpose' ? '引用用途' : aspect === 'assessment' ? '作者评价' : '与本文的异同')

// A page can support several claims. Keep its excerpts together, without merging
// evidence from different papers or dropping distinct passages on the same page.
const locationKey = (e: DomainEvidence) => JSON.stringify([
  e.paperId, e.source, e.page, e.page === null ? e.locator.trim() : '',
])

export default function DomainEdgeEvidence({ analysis, paperMap, onOpenReview, roadmap = false }: {
  analysis: DomainAnalysis
  paperMap: Map<string, DomainPaper>
  onOpenReview: (paperId: string) => void
  roadmap?: boolean
}) {
  const sources = useRef(new Map<string, HTMLDetailsElement>())
  const groups: { key: string; evidence: DomainEvidence; quotes: string[]; uses: Set<string> }[] = []
  const add = (evidence: DomainEvidence, use: string) => {
    const key = locationKey(evidence)
    let group = groups.find(g => g.key === key)
    if (!group) {
      group = { key, evidence, quotes: [], uses: new Set() }
      groups.push(group)
    }
    const normalized = evidence.quote.replace(/\s+/g, ' ').trim()
    if (!group.quotes.some(q => q.replace(/\s+/g, ' ').trim() === normalized)) group.quotes.push(evidence.quote)
    group.uses.add(use)
  }
  analysis.evidence.forEach(e => add(e, tr('连线依据')))
  analysis.discussion?.findings.forEach(f => f.evidence.forEach(e => add(e, aspectLabel(f.aspect))))
  const references = (evidence: DomainEvidence[]) => (
    <span className="domain-evidence-references">
      {[...new Set(evidence.map(locationKey))].map(key => {
        const number = groups.findIndex(g => g.key === key) + 1
        return <button key={key} className="domain-evidence-reference" onClick={() => {
          const details = sources.current.get(key)
          if (!details) return
          details.open = true
          details.querySelector('summary')?.focus({ preventScroll: true })
          details.scrollIntoView({ block: 'nearest' })
        }}>{tr('证据 {0}', { 0: number })}</button>
      })}
    </span>
  )
  const discussion = analysis.discussion
  return <section className="domain-edge-evidence">
    <h2>{tr('连线解读')} · {tr(analysis.certainty === 'explicit' ? '原文明示' : 'AI推断')}</h2>
    <p>{analysis.reason}</p>
    {roadmap && <p className="domain-help">{tr(analysis.certainty === 'explicit'
      ? '关系由所列原文片段支持；可打开来源核对。'
      : '关系为 AI 推断；请结合所列证据与报告中的全文缺口判断。')}</p>}
    {references(analysis.evidence)}
    {discussion && <div className="domain-citation-discussion">
      <h3>{tr('引用语境深读')}</h3>
      <p>{tr('来自引用论文：{0}', { 0: paperMap.get(discussion.citingPaperId)?.title ?? '' })}</p>
      <details className="domain-analysis-scope">
        <summary>{tr('分析范围')}</summary>
        <p>{tr('定位 {0} 处正文语境，本次分析 {1} 处', { 0: discussion.contextsFound, 1: discussion.contextsUsed })}</p>
        {discussion.truncated && <p>{tr('正文语境较多，本次仅分析部分片段。')}</p>}
      </details>
      {discussion.status === 'reference_only' && <p>{tr('仅找到参考文献条目，未定位正文引用语境。')}</p>}
      {discussion.status === 'not_found' && <p>{tr('未定位到可核验的正文引用语境。')}</p>}
      {discussion.status === 'unresolved' && <p>{tr('已定位正文，但未获得有充分证据的解读。')}</p>}
      {(['purpose', 'assessment', 'comparison'] as const).map(aspect => {
        const findings = discussion.findings.filter(f => f.aspect === aspect)
        return <div key={aspect}>
          <h4>{aspectLabel(aspect)}</h4>
          {findings.length === 0 ? <p className="domain-help">{tr('所分析片段中未找到明确表述。')}</p> : findings.map((finding, i) => <div key={i}>
            <p>{finding.text}</p>
            {references(finding.evidence)}
          </div>)}
        </div>
      })}
    </div>}
    <div className="domain-evidence-sources">
      <h3>{tr('证据与出处')}</h3>
      <p className="domain-help">{tr('编号对应上方解读；同一出处的摘录合并展示。')}</p>
      {groups.map(({ key, evidence: e, quotes, uses }, i) => <details key={key}
        ref={element => { if (element) sources.current.set(key, element); else sources.current.delete(key) }}>
        <summary>{tr('证据 {0}', { 0: i + 1 })} · {roadmap
          ? e.page != null ? tr('原文 · PDF 第 {0} 页', { 0: e.page })
            : e.locator.endsWith('#fulltext') ? tr('原文全文 · 页码未知') : tr('来源元数据')
          : tr(e.source === 'review' ? '论文回顾' : e.source === 'wiki' ? '论文Wiki' : '原文')}{!roadmap && ` · ${e.locator}`}</summary>
        <p>{paperMap.get(e.paperId)?.title}</p>
        <p className="domain-evidence-uses">{tr('支持：{0}', { 0: [...uses].join(' · ') })}</p>
        {quotes.map((quote, j) => <blockquote key={j}>{quote}</blockquote>)}
        {roadmap && sourceUrl(e.locator) && <a href={sourceUrl(e.locator)!} target="_blank" rel="noopener noreferrer">{tr('查看论文来源')} ↗</a>}
        {(!roadmap || paperMap.get(e.paperId)?.roadmap?.libraryPaperId) && <button onClick={() => onOpenReview(e.paperId)}>{tr('打开完整回顾')} ↗</button>}
      </details>)}
    </div>
  </section>
}
