import { Children, isValidElement, useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Download, FileText, Loader2, Maximize, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { exportRoadmapReport, getRoadmapReport, type RoadmapReport as Report } from '../api/roadmaps'
import { t as tr } from '../i18n/catalog'
import { sourceUrl } from '../utils/sourceUrl'
import RoadmapDiagram from './RoadmapDiagram'

function ReportCodeBlock({ children }: { children?: ReactNode }) {
  const child = Children.toArray(children)[0]
  if (isValidElement<{ className?: string; children?: ReactNode }>(child) && child.props.className === 'language-mermaid') {
    return <RoadmapDiagram source={String(child.props.children ?? '').trim()} />
  }
  return <pre>{children}</pre>
}

function ReportImage({ src, alt, title }: { src?: string; alt?: string; title?: string }) {
  const [failed, setFailed] = useState(false)
  // Only locally verified official HTML figures or original PDF pages may become images. Ordinary web links
  // remain links; external Markdown images cannot issue tracking requests.
  if (!src || !/^\/api\/roadmaps\/report-assets\/[a-f0-9]{64}\.png$/.test(src)) return <span>{alt || ''}</span>
  return failed ? <span>{tr('原图暂时无法加载')}{alt ? ` · ${alt}` : ''}</span> : <img
    className="roadmap-report-image" src={src} alt={alt || tr('论文原文图')} title={title}
    loading="lazy" decoding="async" onError={() => setFailed(true)} />
}

export default function RoadmapReport({ boardId, boardName, active, visible, runKey }: {
  boardId: string
  boardName: string
  active: boolean
  visible: boolean
  runKey: string
}) {
  const [open, setOpen] = useState(false)
  const [report, setReport] = useState<Report | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState('')
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
  const saveFile = (blob: Blob, extension: string) => {
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${boardName.replace(/[<>:"/\\|?*]/g, '-').trim().slice(0, 80) || 'roadmap'}-research.${extension}`
    anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  const download = () => {
    if (!report || loading) return
    saveFile(new Blob([report.markdown], { type: 'text/markdown;charset=utf-8' }), 'md')
  }
  const downloadBundle = async () => {
    if (!report?.hasAssets || loading || exporting) return
    setExporting(true); setExportError('')
    try { saveFile(await exportRoadmapReport(boardId), 'zip') }
    catch { setExportError(tr('导出报告及图片失败，请重试。')) }
    finally { setExporting(false) }
  }
  const bundleButton = report?.hasAssets && <button disabled={loading || exporting || !!error} onClick={() => void downloadBundle()}>
    {exporting ? <Loader2 size={15} className="spin" /> : <Download size={15} />}{tr('下载报告及图片')}
  </button>
  const status = report ? tr(report.status === 'preview' ? '预览报告 · 应用方案后保存' : '已应用方案的调研报告')
    : tr('七步调研：筛选、全文评估与证据分级，记录来源和缺口。')
  const content = loading ? <p className="roadmap-report-message" role="status"><Loader2 size={16} className="spin" />{tr('加载中…')}</p>
    : error ? <div className="domain-error" role="alert">{error}<button onClick={() => void load()}>{tr('重试')}</button></div>
    : report ? <div className="markdown-notes roadmap-report-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{
        a: ({ href, children }) => {
          const url = sourceUrl(href)
          return url ? <a href={url} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>
        },
        pre: ReportCodeBlock,
        img: ReportImage,
      }}>{report.markdown}</ReactMarkdown>
    </div> : <p className="roadmap-report-message">{tr('暂无调研报告。请先生成路线图，完成后可查看和下载。')}</p>
  return <>
    <section id="roadmap-report-panel" role="tabpanel" aria-labelledby="roadmap-report-tab"
      className="roadmap-report-panel" hidden={!visible}>
      <header className="roadmap-report-panel-header">
        <div>
          <h2><FileText size={19} />{boardName} · {tr('总体调研报告')}</h2>
          <p>{status}</p>
        </div>
        <div className="roadmap-report-actions">
          <button disabled={!report || loading || !!error} onClick={() => setOpen(true)}>
            <Maximize size={15} />{tr('展开报告')}
          </button>
          <button className="domain-primary" disabled={!report || loading || !!error} onClick={download}>
            <Download size={15} />{tr('下载 Markdown')}
          </button>
          {bundleButton}
        </div>
      </header>
      {exportError && <p role="alert" className="domain-error">{exportError}</p>}
      <div className="roadmap-report-body" tabIndex={0} aria-label={tr('总体调研报告')}>
        <article className="roadmap-report-document">{content}</article>
      </div>
      <footer className="roadmap-report-panel-footer">{tr('全文未获取或证据不足的部分，会在报告中列为缺口。')}</footer>
    </section>
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
            <p>{status}</p>
          </div>
          <button ref={closeButton} aria-label={tr('关闭')} onClick={() => setOpen(false)}><X size={18} /></button>
        </header>
        {exportError && <p role="alert" className="domain-error">{exportError}</p>}
        <div className="roadmap-report-body">
          {content}
        </div>
        <footer>
          <span>{tr('全文未获取或证据不足的部分，会在报告中列为缺口。')}</span>
          <button className="domain-primary" disabled={!report || loading || !!error} onClick={download}><Download size={15} />{tr('下载 Markdown')}</button>
          {bundleButton}
        </footer>
      </section>
    </div>}
  </>
}
