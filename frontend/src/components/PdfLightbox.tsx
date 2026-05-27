// Full-screen PDF reader for the [回顾] page, opened from the 首页预览
// block so the user can inspect the original PDF without leaving the
// review context.
//
// Built on react-pdf (pdf.js under the hood). Three things distinguish
// it from the existing image Lightbox:
//
//   1. Multi-page: renders every page in a vertical scroll stack.
//   2. Default fit-to-width: page width fills the available viewport on
//      open. Once the user zooms, we stop auto-fitting so window resizes
//      don't surprise-zoom them.
//   3. Position-preserving zoom: pressing +/− re-renders pages at a new
//      scale (which changes the inner scrollHeight); we capture the
//      logical center of the viewport before the change and rescroll
//      after the new layout stabilizes via a ResizeObserver. The user
//      stays "at the same paragraph" instead of jumping to the top.

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import {
  ZoomIn,
  ZoomOut,
  X,
  Maximize2,
  ExternalLink,
  Loader2,
  AlertTriangle,
} from 'lucide-react'
import { Document, Page, pdfjs } from 'react-pdf'
import 'react-pdf/dist/Page/AnnotationLayer.css'
import 'react-pdf/dist/Page/TextLayer.css'

// pdf.js needs a worker. Use Vite's `new URL(..., import.meta.url)` so
// the bundler resolves to the actual file under node_modules at build
// time — no CDN dependency, no manual copy step.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()

const MIN_SCALE = 0.4
const MAX_SCALE = 4
const SCALE_STEP = 0.2
const PAGE_GAP_PX = 16
// Reserve space on each side so the page doesn't touch the scrollbar /
// container edge.
const HORIZONTAL_PADDING = 32

function clampScale(v: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, Number(v.toFixed(3))))
}

interface Props {
  src: string
  title?: string
  /** Optional URL for "在新标签页打开" so the user can fall back to the
   *  browser-native viewer if our renderer can't open this PDF. */
  externalHref?: string
  onClose: () => void
}

export default function PdfLightbox({ src, title, externalHref, onClose }: Props) {
  // null = not loaded yet; 0 = load failed
  const [numPages, setNumPages] = useState<number | null>(null)
  const [pageWidth, setPageWidth] = useState<number | null>(null)
  // scale === null means "auto fit to width on next layout pass". Any
  // explicit zoom action sets a numeric scale and we stop auto-fitting.
  const [scale, setScale] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const scrollRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  // Each entry: the natural unscaled page width as reported by pdf.js on
  // page load. We pick the first one for the fit-to-width calculation.
  const naturalWidthRef = useRef<number | null>(null)
  // When a zoom action is in flight, we remember the logical position
  // (fraction of scrollHeight) and reapply it once the new layout lands.
  const pendingAnchorRef = useRef<number | null>(null)

  // ── load options ────────────────────────────────────────────────
  // Memoized so react-pdf's <Document> doesn't see a new object on
  // every render (which would re-trigger PDF loading).
  const loadOptions = useMemo(() => ({ url: src }), [src])

  // ── keyboard shortcuts ─────────────────────────────────────────
  const handleZoomIn = useCallback(() => {
    captureAnchor()
    setScale(prev => clampScale((prev ?? autoFitScale()) + SCALE_STEP))
  }, [])
  const handleZoomOut = useCallback(() => {
    captureAnchor()
    setScale(prev => clampScale((prev ?? autoFitScale()) - SCALE_STEP))
  }, [])
  const handleFit = useCallback(() => {
    captureAnchor()
    setScale(null) // back to auto fit-to-width
  }, [])

  // Capture the logical scroll anchor (fraction of scrollHeight pointing
  // at the visual center) before a zoom is applied. Restored after the
  // layout stabilizes — see ResizeObserver effect below.
  function captureAnchor() {
    const el = scrollRef.current
    if (!el || el.scrollHeight === 0) return
    const center = el.scrollTop + el.clientHeight / 2
    pendingAnchorRef.current = center / el.scrollHeight
  }

  // Compute the auto fit-to-width scale relative to the natural page
  // width returned by pdf.js. Falls back to 1 if we don't know it yet.
  function autoFitScale(): number {
    const nat = naturalWidthRef.current
    const w = pageWidth
    if (!nat || !w) return 1
    return clampScale(w / nat)
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose()
        return
      }
      if (e.target instanceof HTMLElement && /^(INPUT|TEXTAREA)$/.test(e.target.tagName)) {
        return
      }
      if (e.key === '+' || e.key === '=') {
        e.preventDefault()
        handleZoomIn()
      } else if (e.key === '-' || e.key === '_') {
        e.preventDefault()
        handleZoomOut()
      } else if (e.key === '0') {
        e.preventDefault()
        handleFit()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [handleFit, handleZoomIn, handleZoomOut, onClose])

  // Block page scroll while the lightbox is open.
  useEffect(() => {
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = prev }
  }, [])

  // ── viewport width tracking ───────────────────────────────────
  // The page width we hand to <Page width={...}> is the inner scroll
  // container's clientWidth minus our horizontal padding. We re-measure
  // on resize.
  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const measure = () => {
      const w = el.clientWidth - HORIZONTAL_PADDING * 2
      setPageWidth(Math.max(200, w))
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // ── re-anchor after zoom ──────────────────────────────────────
  // The inner content height changes after a scale change. Observe it
  // and, once it's settled, restore the logical anchor we captured
  // pre-zoom so the user stays at the same paragraph.
  useEffect(() => {
    const scrollEl = scrollRef.current
    const contentEl = contentRef.current
    if (!scrollEl || !contentEl) return

    let frame: number | null = null
    const tryApply = () => {
      const anchor = pendingAnchorRef.current
      if (anchor == null) return
      // We want the visual center to land at the same logical fraction.
      // newScrollTop = anchor * newScrollHeight - clientHeight/2
      const target = anchor * scrollEl.scrollHeight - scrollEl.clientHeight / 2
      scrollEl.scrollTop = Math.max(0, target)
      pendingAnchorRef.current = null
    }

    const ro = new ResizeObserver(() => {
      if (pendingAnchorRef.current == null) return
      // The content can grow/shrink in multiple ticks while pages render
      // their canvases. Debounce via RAF so we only seek once per frame.
      if (frame != null) cancelAnimationFrame(frame)
      frame = requestAnimationFrame(tryApply)
    })
    ro.observe(contentEl)
    return () => {
      ro.disconnect()
      if (frame != null) cancelAnimationFrame(frame)
    }
  }, [])

  // ── wheel zoom (Ctrl/⌘ + scroll, the standard PDF reader binding) ─
  const onWheel = (e: React.WheelEvent) => {
    if (!(e.ctrlKey || e.metaKey)) return
    e.preventDefault()
    if (e.deltaY < 0) handleZoomIn()
    else handleZoomOut()
  }

  // ── effective scale that we pass to <Page> ────────────────────
  // null → fit width; otherwise the explicit user-chosen value.
  const effectiveScale = scale ?? autoFitScale()
  const widthForPage =
    scale == null && pageWidth != null
      ? pageWidth // let react-pdf compute scale internally from width prop
      : naturalWidthRef.current != null
        ? naturalWidthRef.current * effectiveScale
        : pageWidth ?? 0

  const pct = Math.round(effectiveScale * 100)

  return (
    <div
      className="fixed inset-0 z-[70] flex flex-col bg-slate-950/95 backdrop-blur-sm"
      onClick={onClose}
    >
      {/* Header */}
      <header
        className="flex items-center gap-2 border-b border-slate-800/80 bg-slate-950/80 px-4 py-2 text-slate-200"
        onClick={e => e.stopPropagation()}
      >
        <span className="text-xs text-slate-300 font-medium truncate max-w-[42vw]" title={title}>
          {title || '原始 PDF'}
        </span>
        {numPages != null && numPages > 0 && (
          <span className="text-[11px] text-slate-500">· {numPages} 页</span>
        )}
        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={handleZoomOut}
            disabled={effectiveScale <= MIN_SCALE + 1e-3}
            title="缩小 (-)"
            className={btnCls}
          >
            <ZoomOut size={14} />
          </button>
          <button
            onClick={handleFit}
            title="适应宽度 (0)"
            className={`${btnCls} min-w-14 tabular-nums`}
          >
            {pct}%
          </button>
          <button
            onClick={handleZoomIn}
            disabled={effectiveScale >= MAX_SCALE - 1e-3}
            title="放大 (+)"
            className={btnCls}
          >
            <ZoomIn size={14} />
          </button>
          <button
            onClick={handleFit}
            title="适应窗口宽度"
            className={btnCls}
          >
            <Maximize2 size={14} />
          </button>
          {externalHref && (
            <a
              href={externalHref}
              target="_blank"
              rel="noreferrer"
              onClick={e => e.stopPropagation()}
              title="在新标签页打开"
              className={btnCls}
            >
              <ExternalLink size={14} />
            </a>
          )}
          <button
            onClick={onClose}
            title="关闭 (Esc)"
            className={`${btnCls} ml-1 hover:border-red-500/60 hover:text-red-200`}
          >
            <X size={14} />
          </button>
        </div>
      </header>

      {/* PDF surface */}
      <div
        ref={scrollRef}
        onWheel={onWheel}
        onClick={onClose}
        className="min-h-0 flex-1 overflow-auto"
      >
        <div
          ref={contentRef}
          className="flex flex-col items-center"
          style={{ padding: `${PAGE_GAP_PX}px ${HORIZONTAL_PADDING}px` }}
          onClick={e => e.stopPropagation()}
        >
          {error ? (
            <div className="my-16 flex flex-col items-center gap-3 text-rose-300">
              <AlertTriangle size={28} />
              <div className="text-sm">{error}</div>
              {externalHref && (
                <a
                  href={externalHref}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs text-indigo-300 underline hover:text-indigo-200"
                >
                  改为在新标签页打开 →
                </a>
              )}
            </div>
          ) : (
            <Document
              file={loadOptions}
              onLoadSuccess={({ numPages: n }) => {
                setNumPages(n)
                setError(null)
              }}
              onLoadError={err => {
                console.error('PDF load failed', err)
                setError(`PDF 加载失败：${err.message || '未知错误'}`)
              }}
              loading={
                <div className="my-16 flex items-center gap-2 text-slate-400 text-sm">
                  <Loader2 size={14} className="animate-spin" /> 正在解析 PDF…
                </div>
              }
              error={
                // suppressed; we render our own error block above
                <span />
              }
            >
              {numPages != null &&
                Array.from({ length: numPages }, (_, i) => (
                  <div
                    key={i}
                    className="mb-4 rounded-md border border-slate-800 bg-white shadow-2xl"
                  >
                    <Page
                      pageNumber={i + 1}
                      width={widthForPage || undefined}
                      renderAnnotationLayer
                      renderTextLayer
                      onLoadSuccess={page => {
                        // Record the natural width from the first page so
                        // explicit-scale rendering can compute target
                        // widths consistently.
                        if (naturalWidthRef.current == null) {
                          naturalWidthRef.current = page.originalWidth
                        }
                      }}
                      loading={
                        <div className="flex h-[60vh] w-full items-center justify-center text-slate-400 text-sm">
                          <Loader2 size={14} className="animate-spin mr-2" />
                          渲染第 {i + 1} 页…
                        </div>
                      }
                    />
                  </div>
                ))}
            </Document>
          )}
        </div>
      </div>

      {/* Footer hint */}
      <footer
        className="border-t border-slate-800/80 bg-slate-950/70 px-4 py-1.5 text-center text-[11px] text-slate-500"
        onClick={e => e.stopPropagation()}
      >
        Ctrl / ⌘ + 滚轮缩放 · + / − 键缩放 · 0 适应宽度 · Esc 关闭
      </footer>
    </div>
  )
}

const btnCls =
  'inline-flex h-8 min-w-8 items-center justify-center rounded-md border border-slate-800 bg-slate-950/40 px-2 text-xs text-slate-300 transition-colors hover:border-slate-700 hover:text-white disabled:cursor-not-allowed disabled:opacity-40'
