import { useCallback, useEffect, useRef, useState } from 'react'
import { Download, FileText, Loader2, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { getRoadmapReport, type RoadmapReport as Report } from '../api/roadmaps'
import { t as tr } from '../i18n/catalog'
import { sourceUrl } from '../utils/sourceUrl'

export default function RoadmapReport({ boardId, boardName, active, runKey }: {
  boardId: string
  boardName: string
  active: boolean
  runKey: string
}) {
  const [open, setOpen] = useState(false)
  const [report, setReport] = useState<Report | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const sequence = useRef(0)
  const dialog = useRef<HTMLElement>(null)
  const closeButton = useRef<HTMLButtonElement>(null)
  const load = useCallback(async (signal?: AbortSignal) => {
    const version = ++sequence.current
    setLoading(true)
    setError('')
    try {
      const next = await getRoadmapReport(boardId, signal)
      if (version === sequence.current && !signal?.aborted) setReport(next)
    } catch {
      if (version === sequence.current && !signal?.aborted) {
        setReport(null)
        setError(tr('调研报告加载失败，请重试。'))
      }
    } finally {
      if (version === sequence.current && !signal?.aborted) setLoading(false)
    }
  }, [boardId])
  useEffect(() => {
    if (!active) return
    const controller = new AbortController()
    const timer = setTimeout(() => void load(controller.signal), 0)
    return () => { clearTimeout(timer); controller.abort() }
  }, [active, load, runKey])
  useEffect(() => {
    if (!active || !open) return
    const previous = document.activeElement as HTMLElement | null
    closeButton.current?.focus()
    return () => previous?.focus({ preventScroll: true })
  }, [active, open])
  const download = () => {
    if (!report || loading) return
    const url = URL.createObjectURL(new Blob([report.markdown], { type: 'text/markdown;charset=utf-8' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${boardName.replace(/[<>:"/\\|?*]/g, '-').trim().slice(0, 80) || 'roadmap'}-research.md`
    anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  return <>
    <button className="roadmap-report-trigger" onClick={() => { setOpen(true); void load() }}>
      <FileText size={15} />{tr('调研报告')}
    </button>
    {active && open && <div className="domain-modal-backdrop" onClick={() => setOpen(false)}>
      <section ref={dialog} role="dialog" aria-modal="true" aria-label={tr('调研报告')}
        className="domain-modal roadmap-report-modal" onClick={event => event.stopPropagation()}
        onKeyDown={event => {
          event.stopPropagation()
          if (event.key === 'Escape') { event.preventDefault(); setOpen(false); return }
          if (event.key !== 'Tab') return
          const controls = Array.from(dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href]') ?? [])
          const first = controls[0], last = controls.at(-1)
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
        }}>
        <header>
          <div><h2>{boardName} · {tr('调研报告')}</h2>
            <p>{report ? tr(report.status === 'preview' ? '预览报告 · 应用方案后保存' : '已应用方案的调研报告') : tr('七步调研：筛选、全文评估与证据分级，记录来源和缺口。')}</p>
          </div>
          <button ref={closeButton} aria-label={tr('关闭')} onClick={() => setOpen(false)}><X size={18} /></button>
        </header>
        <div className="roadmap-report-body">
          {loading ? <p role="status"><Loader2 size={14} className="spin" />{tr('加载中…')}</p>
            : error ? <div className="domain-error" role="alert">{error}<button onClick={() => void load()}>{tr('重试')}</button></div>
            : report ? <div className="markdown-notes roadmap-report-markdown">
              <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{
                a: ({ href, children }) => {
                  const url = sourceUrl(href)
                  return url ? <a href={url} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>
                },
                img: ({ alt }) => <span>{alt || ''}</span>,
              }}>{report.markdown}</ReactMarkdown>
            </div> : <p>{tr('暂无调研报告。请先生成路线图，完成后可查看和下载。')}</p>}
        </div>
        <footer>
          <span>{tr('全文未获取或证据不足的部分，会在报告中列为缺口。')}</span>
          <button className="domain-primary" disabled={!report || loading || !!error} onClick={download}><Download size={15} />{tr('下载 Markdown')}</button>
        </footer>
      </section>
    </div>}
  </>
}
