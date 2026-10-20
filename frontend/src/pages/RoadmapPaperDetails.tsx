import { Download, ExternalLink, Loader2 } from 'lucide-react'
import type { DomainPaper } from '../api/domains'
import { t as tr } from '../i18n/catalog'
import { sourceUrl } from '../utils/sourceUrl'

export function RoadmapPaperStatus({ paper }: { paper: DomainPaper }) {
  const meta = paper.roadmap
  if (!meta) return null
  const label = meta.libraryStatus === 'in_library' ? '已入库'
    : meta.downloadStatus === 'running' ? '下载中'
    : meta.downloadStatus === 'queued' ? '等待下载'
    : meta.downloadStatus === 'failed' ? '下载失败'
    : meta.libraryStatus === 'no_arxiv' ? '非 arXiv · 仅来源' : '未入库'
  return <span className={`roadmap-library-status ${meta.libraryStatus}${meta.downloadStatus === 'failed' ? ' is-failed' : ''}`} title={meta.downloadError || undefined}>{tr(label)}</span>
}

export default function RoadmapPaperDetails({
  paper,
  onOpenReview,
  onCollect,
  collectDisabled = false,
}: {
  paper: DomainPaper
  onOpenReview: (id: string) => void
  onCollect?: (id: string) => void
  collectDisabled?: boolean
}) {
  const meta = paper.roadmap
  if (!meta) return null
  const url = sourceUrl(meta.sourceUrl)
  const collecting = meta.downloadStatus === 'queued' || meta.downloadStatus === 'running'
  const collectable = meta.libraryStatus === 'not_imported' && !!meta.arxivId
  return <section className="roadmap-paper-details">
    <h2>{paper.title}</h2>
    <div className="roadmap-paper-meta">
      <RoadmapPaperStatus paper={paper} />
      {meta.branch && <span>{meta.branch}</span>}
      {collectable && onCollect && <button
        type="button"
        className="roadmap-collect-paper"
        disabled={collectDisabled || collecting}
        onClick={() => onCollect(paper.id)}
      >
        {collecting ? <Loader2 size={12} className="spin" /> : <Download size={12} />}
        {tr(collecting ? '正在收集' : '收集入库')}
      </button>}
    </div>
    <h3>{tr('入选理由 · AI 推断')}</h3>
    <p>{meta.reason || paper.sections.core_contribution}</p>
    {paper.sections.abstract_summary && <><h3>{tr('来源摘要')}</h3><p>{paper.sections.abstract_summary}</p></>}
    <div className="roadmap-source-links">
      {url && <a href={url} target="_blank" rel="noopener noreferrer"><ExternalLink size={13} />{tr('查看论文来源')}</a>}
      {meta.libraryPaperId && <button onClick={() => onOpenReview(meta.libraryPaperId!)}>{tr('打开完整回顾')} ↗</button>}
    </div>
    {meta.libraryPaperId && meta.reviewReady === false && <p className="domain-help">{tr('已入库，尚未生成论文回顾；可在资料页处理。')}</p>}
    {meta.downloadError && <p className="domain-error">{meta.downloadError}</p>}
  </section>
}
