// Right-side PDF reader for the [回顾] page. Replaces the earlier
// full-screen lightbox so the structured extraction column on the left
// stays visible while the user cross-references the PDF on the right.
//
// Key UX commitments:
//   1. Anchored to the right edge of the viewport. Width is capped so
//      the structured "核心贡献" column on the left is never covered.
//   2. Click anywhere outside the panel dismisses it.
//   3. State (scroll position + zoom) is bubbled up via onClose so the
//      page can restore it the next time the same PDF is opened.
//   4. Position-preserving zoom (same logical anchor as the original
//      lightbox) so the user stays at the same paragraph across zooms.

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

// pdf.js worker: bundled via Vite's `new URL(..., import.meta.url)`.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString()

const MIN_SCALE = 0.4
const MAX_SCALE = 4
const SCALE_STEP = 0.2
const PAGE_GAP_PX = 16
const HORIZONTAL_PADDING = 32

function clampScale(v: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, Number(v.toFixed(3))))
}

/** State the parent persists across open/close cycles. `scale === null`
 *  means "fit to width" — preserved so the user's chosen zoom mode
 *  (auto-fit vs. explicit %) survives re-open. */
export interface PdfViewState {
  scrollTop: number
  scale: number | null
}

interface Props {
  src: string
  title?: string
  externalHref?: string
  /** When provided, restores scroll position + scale on mount. Null /
   *  undefined = first open: use defaults (fit-to-width, scrollTop=0). */
  initialState?: PdfViewState | null
  /** Called when the panel wants to close. The parent should both
   *  unmount the panel AND persist this state for the next open. */
  onClose: (finalState: PdfViewState) => void
}

export default function PdfSidePanel({
  src,
  title,
  externalHref,
  initialState,
  onClose,
}: Props) {
  const [numPages, setNumPages] = useState<number | null>(null)
  const [pageWidth, setPageWidth] = useState<number | null>(null)
  const [scale, setScale] = useState<number | null>(initialState?.scale ?? null)
  const [error, setError] = useState<string | null>(null)

  const panelRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  // The first page's intrinsic (PDF-units) width, captured once it
  // renders. Kept as state — not a ref — so we don't read it during
  // render via `.current` (lint rule `react-hooks/refs`).
  const [naturalWidth, setNaturalWidth] = useState<number | null>(null)

  // Zoom anchor (set just before scale changes, applied after layout settles).
  const pendingAnchorRef = useRef<number | null>(null)

  // Restore-on-first-render: hold the initial scrollTop until pages have
  // rendered enough content for it to make sense to scroll there.
  const pendingInitialScrollRef = useRef<number | null>(
    initialState?.scrollTop ?? null,
  )

  const loadOptions = useMemo(() => ({ url: src }), [src])

  // ── auto-fit math ────────────────────────────────────────────────
  const autoFitScale = useCallback((): number => {
    if (!naturalWidth || !pageWidth) return 1
    return clampScale(pageWidth / naturalWidth)
  }, [naturalWidth, pageWidth])

  // ── anchor capture / zoom handlers ──────────────────────────────
  const captureAnchor = useCallback(() => {
    const el = scrollRef.current
    if (!el || el.scrollHeight === 0) return
    const center = el.scrollTop + el.clientHeight / 2
    pendingAnchorRef.current = center / el.scrollHeight
  }, [])

  const handleZoomIn = useCallback(() => {
    captureAnchor()
    setScale(prev => clampScale((prev ?? autoFitScale()) + SCALE_STEP))
  }, [autoFitScale, captureAnchor])

  const handleZoomOut = useCallback(() => {
    captureAnchor()
    setScale(prev => clampScale((prev ?? autoFitScale()) - SCALE_STEP))
  }, [autoFitScale, captureAnchor])

  const handleFit = useCallback(() => {
    captureAnchor()
    setScale(null)
  }, [captureAnchor])

  // ── close: bundle current state up to parent ────────────────────
  // Use a ref-cell for `scale` so the close path always reads the
  // latest value even when invoked from a long-lived listener.
  const scaleRef = useRef(scale)
  useEffect(() => { scaleRef.current = scale }, [scale])

  const requestClose = useCallback(() => {
    onClose({
      scrollTop: scrollRef.current?.scrollTop ?? 0,
      scale: scaleRef.current,
    })
  }, [onClose])

  // ── keyboard shortcuts ─────────────────────────────────────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (
        e.target instanceof HTMLElement &&
        /^(INPUT|TEXTAREA)$/.test(e.target.tagName)
      ) {
        return
      }
      if (e.key === 'Escape') { e.preventDefault(); requestClose(); return }
      if (e.key === '+' || e.key === '=') { e.preventDefault(); handleZoomIn() }
      else if (e.key === '-' || e.key === '_') { e.preventDefault(); handleZoomOut() }
      else if (e.key === '0') { e.preventDefault(); handleFit() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [handleFit, handleZoomIn, handleZoomOut, requestClose])

  // ── click-outside dismiss ──────────────────────────────────────
  // The mousedown listener is added in an effect that runs *after* the
  // open click has bubbled, so the click that mounted the panel can't
  // accidentally close it on the same tick.
  useEffect(() => {
    const onMouseDown = (e: MouseEvent) => {
      const panel = panelRef.current
      if (!panel) return
      const target = e.target as Node | null
      if (!target || panel.contains(target)) return
      requestClose()
    }
    document.addEventListener('mousedown', onMouseDown)
    return () => document.removeEventListener('mousedown', onMouseDown)
  }, [requestClose])

  // ── available-width tracking ───────────────────────────────────
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

  // ── post-layout adjustments: zoom anchor + initial-scroll restore ─
  useEffect(() => {
    const scrollEl = scrollRef.current
    const contentEl = contentRef.current
    if (!scrollEl || !contentEl) return

    let frame: number | null = null
    const apply = () => {
      // Restore the persisted scroll on first mount, once enough pages
      // have rendered for the position to be valid.
      const initialTop = pendingInitialScrollRef.current
      if (initialTop != null) {
        if (
          scrollEl.scrollHeight >=
          initialTop + scrollEl.clientHeight * 0.5
        ) {
          scrollEl.scrollTop = initialTop
          pendingInitialScrollRef.current = null
        }
      }
      // Re-apply zoom anchor after a scale change.
      const anchor = pendingAnchorRef.current
      if (anchor != null && scrollEl.scrollHeight > 0) {
        const target = anchor * scrollEl.scrollHeight - scrollEl.clientHeight / 2
        scrollEl.scrollTop = Math.max(0, target)
        pendingAnchorRef.current = null
      }
    }

    const ro = new ResizeObserver(() => {
      if (frame != null) cancelAnimationFrame(frame)
      frame = requestAnimationFrame(apply)
    })
    ro.observe(contentEl)
    return () => {
      ro.disconnect()
      if (frame != null) cancelAnimationFrame(frame)
    }
  }, [])

  // ── wheel zoom (Ctrl/⌘ + scroll) ───────────────────────────────
  const onWheel = (e: React.WheelEvent) => {
    if (!(e.ctrlKey || e.metaKey)) return
    e.preventDefault()
    if (e.deltaY < 0) handleZoomIn()
    else handleZoomOut()
  }

  // ── render-time width plumbing ─────────────────────────────────
  const effectiveScale = scale ?? autoFitScale()
  const widthForPage =
    scale == null && pageWidth != null
      ? pageWidth
      : naturalWidth != null
        ? naturalWidth * effectiveScale
        : pageWidth ?? 0

  const pct = Math.round(effectiveScale * 100)

  return (
    <div
      ref={panelRef}
      className="fixed right-0 top-0 bottom-0 z-[60] flex flex-col bg-slate-950 border-l border-slate-800/80 shadow-2xl"
      // Width sized so that on common laptop / desktop widths the panel
      // covers exactly the 个人笔记 + 首页预览 columns and stops at the
      // right edge of 核心贡献 / structured extraction. Clamped so
      // narrow viewports stay usable.
      style={{ width: 'min(50rem, 60vw)' }}
    >
      {/* Header */}
      <header className="flex items-center gap-2 border-b border-slate-800/80 bg-slate-950/80 px-4 py-2 text-slate-200">
        <span
          className="min-w-0 flex-1 truncate text-xs font-medium text-slate-300"
          title={title}
        >
          {title || '原始 PDF'}
        </span>
        {numPages != null && numPages > 0 && (
          <span className="text-[11px] text-slate-500 shrink-0">· {numPages} 页</span>
        )}
        <div className="ml-auto flex items-center gap-1 shrink-0">
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
          <button onClick={handleFit} title="适应宽度" className={btnCls}>
            <Maximize2 size={14} />
          </button>
          {externalHref && (
            <a
              href={externalHref}
              target="_blank"
              rel="noreferrer"
              onClick={e => e.stopPropagation()}
              title="新标签页打开"
              className={btnCls}
            >
              <ExternalLink size={14} />
            </a>
          )}
          <button
            onClick={requestClose}
            title="关闭（保留位置）"
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
        className="min-h-0 flex-1 overflow-auto"
      >
        <div
          ref={contentRef}
          className="flex flex-col items-center"
          style={{ padding: `${PAGE_GAP_PX}px ${HORIZONTAL_PADDING}px` }}
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
              error={<span />}
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
                        // Only adopt the very first page's natural width
                        // (multi-page PDFs occasionally have outliers).
                        setNaturalWidth(prev => prev ?? page.originalWidth)
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

      {/* Footer */}
      <footer className="border-t border-slate-800/80 bg-slate-950/70 px-4 py-1.5 text-center text-[11px] text-slate-500">
        Ctrl/⌘ + 滚轮缩放 · + / − 键缩放 · 0 适应宽度 · Esc 或点击外部关闭并保存位置
      </footer>
    </div>
  )
}

const btnCls =
  'inline-flex h-8 min-w-8 items-center justify-center rounded-md border border-slate-800 bg-slate-950/40 px-2 text-xs text-slate-300 transition-colors hover:border-slate-700 hover:text-white disabled:cursor-not-allowed disabled:opacity-40'
