import { useEffect, useId, useRef, useState } from 'react'
import { Download, Loader2 } from 'lucide-react'
import { exportSvgGraph } from '../lib/svgGraphExport'
import DOMPurify from 'dompurify'
import { t as tr } from '../i18n/catalog'
import { useTheme } from '../i18n/preferences'

let renderQueue = Promise.resolve()
const boundedFlowchart = (source: string) => source.length <= 20000 && source.split('\n').length <= 500 &&
  /^(?:flowchart|graph)\s+(?:TB|TD|BT|LR|RL)\b/.test(source.trim()) &&
  (source.match(/-->|==>|-\.->/g)?.length ?? 0) <= 120 &&
  !/%%\{|<|@\{|https?:|(^|[;\n])\s*(?:click|style|classDef|linkStyle)\b/i.test(source)

function renderFlowchart(id: string, source: string, theme: 'dark' | 'light') {
  const result = renderQueue.then(async () => {
    const { default: mermaid } = await import('mermaid')
    mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', suppressErrorRendering: true,
      theme: theme === 'dark' ? 'dark' : 'default', maxTextSize: 20000, maxEdges: 120, htmlLabels: false,
      flowchart: { useMaxWidth: true }, fontFamily: 'Arial, sans-serif',
    })
    // The report is also mounted behind its expanded dialog. Measure in a
    // separate, noninteractive host so a hidden panel cannot produce zero sizes.
    const host = document.createElement('div')
    host.style.cssText = 'position:fixed;left:-100000px;top:0;width:1200px;visibility:hidden;pointer-events:none'
    document.body.append(host)
    try {
      const { svg } = await mermaid.render(id, source, host)
      return DOMPurify.sanitize(svg, { USE_PROFILES: { svg: true, svgFilters: true },
        FORBID_TAGS: ['foreignObject', 'a', 'image', 'script', 'iframe', 'object', 'embed'],
      })
    } finally { host.remove() }
  })
  renderQueue = result.then(() => undefined, () => undefined)
  return result
}

export default function RoadmapDiagram({ source }: { source: string }) {
  const id = `roadmap-diagram-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`
  const theme = useTheme()
  const [result, setResult] = useState<{ source: string; theme: string; svg: string | null } | null>(null)
  const diagram = useRef<HTMLDivElement>(null)
  const exportBusy = useRef(false)
  const [exporting, setExporting] = useState(false)
  const [exportFeedback, setExportFeedback] = useState<{ source: string; theme: string; message: string; failed: boolean } | null>(null)
  const supported = boundedFlowchart(source)
  useEffect(() => {
    if (!supported) return
    let cancelled = false
    void renderFlowchart(id, source, theme).then(svg => {
      if (!cancelled) setResult({ source, theme, svg })
    }).catch(() => { if (!cancelled) setResult({ source, theme, svg: null }) })
    return () => { cancelled = true }
  }, [id, source, supported, theme])
  const download = async () => {
    const svg = diagram.current?.querySelector('svg')
    if (!svg || exportBusy.current) return
    exportBusy.current = true
    setExporting(true)
    setExportFeedback(null)
    try {
      const size = await exportSvgGraph(svg, tr('识途') + '-flowchart')
      setExportFeedback({ source, theme, failed: false,
        message: tr('已导出 {0} × {1} 像素 · 300 DPI', { 0: size.width, 1: size.height }) +
          (size.limited ? ' · ' + tr('大图已按安全像素上限缩放。') : '') })
    } catch (error) {
      setExportFeedback({ source, theme, failed: true, message: error instanceof Error ? tr(error.message) : tr('图像导出失败，请重试。') })
    } finally { exportBusy.current = false; setExporting(false) }
  }
  const feedback = exportFeedback?.source === source && exportFeedback.theme === theme ? exportFeedback : null
  const current = result?.source === source && result.theme === theme ? result : null
  if (!supported || current?.svg === null) return <div className="roadmap-diagram-fallback">
    <p>{tr('流程图无法渲染，已保留原始定义。')}</p><pre><code>{source.slice(0, 20000)}</code></pre>
  </div>
  return <div className="roadmap-diagram">
    {current?.svg && <div className="roadmap-diagram-export">
      <button type="button" disabled={exporting} onClick={() => void download()}
        aria-label={tr('下载流程图（高清 PNG）')}
        title={tr('下载完整图；像素尺寸决定清晰度，300 DPI 用于打印。')}>
        {exporting ? <Loader2 size={14} className="spin" /> : <Download size={14} />}
        {tr(exporting ? '正在导出…' : '下载图 PNG')}
      </button>
      {feedback && <span role={feedback.failed ? 'alert' : 'status'}>{feedback.message}</span>}
    </div>}
    <div ref={diagram} role="img" aria-label={tr('技术发展流程图')}>
      {current?.svg ? <div dangerouslySetInnerHTML={{ __html: current.svg }} /> : <p role="status">{tr('正在绘制流程图…')}</p>}
    </div>
  </div>
}
