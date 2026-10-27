import { useCallback, useEffect, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Download,
  ExternalLink,
  FileText,
  Frame,
  Hand,
  History,
  Link2,
  Loader2,
  LockKeyhole,
  Maximize,
  MousePointer2,
  Network,
  Plus,
  Redo2,
  RefreshCw,
  Search,
  Sparkles,
  Star,
  Trash2,
  Type,
  Undo2,
  UnlockKeyhole,
  Workflow,
  X,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
import { t as tr } from '../i18n/catalog'
import { useLocale } from '../i18n/preferences'
import {
  domainApi,
  newNode,
  freePosition,
  type CanvasNode,
  type DomainBoard,
  type DomainPaper,
  type ResearchRelationPolicy,
  type ReviewField,
} from '../api/domains'
import { arrangeRoadmap, roadmapApi, type RoadmapLayoutOrientation } from '../api/roadmaps'
import { localApiErrorMessage } from '../api/errors'
import { addPaperCategory, deletePaperCategory, renamePaperCategory } from '../api/client'
import { useDomainWorkspace } from '../hooks/useDomainWorkspace'
import { useDomainAgent } from '../hooks/useDomainAgent'
import { useDomainInspectorResize } from '../hooks/useDomainInspectorResize'
import { useRoadmapImport } from '../hooks/useRoadmapImport'
import DomainEdgeEvidence from './DomainEdgeEvidence'
import RoadmapPaperDetails, { RoadmapPaperStatus } from './RoadmapPaperDetails'
import RoadmapReport from './RoadmapReport'
import { sourceUrl } from '../utils/sourceUrl'
import { roadmapEdgeGeometry } from '../lib/roadmapGeometry'
import { availableImportPapers, importedPaperIds } from '../lib/domainImport'
import { exportDomainGraph } from '../lib/domainGraphExport'
import './DomainPage.css'

const FIELDS: [ReviewField, string][] = [
  ['core_contribution', '核心贡献'],
  ['abstract_summary', '摘要'],
  ['problem', '研究问题'],
  ['motivation', '研究动机'],
]
const id = () => crypto.randomUUID()
const clampZoom = (z: number, minimum = 0.15) => Math.min(2.5, Math.max(minimum, z))
type Point = { x: number; y: number }
type Gesture = {
  mode: 'move' | 'pan' | 'select' | 'resize'
  start: Point
  board: DomainBoard
  ids: Set<string>
  nodeId?: string
  additive?: boolean
}

function CanvasItem({
  node,
  selected,
  readOnly,
  children,
  onStart,
  onSize,
  roadmapRole,
}: {
  node: CanvasNode
  selected: boolean
  readOnly: boolean
  children: React.ReactNode
  onStart: (e: ReactPointerEvent, node: CanvasNode, resize?: boolean) => void
  onSize: (id: string, width: number, height: number) => void
  roadmapRole?: CanvasNode['roadmapRole']
}) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const element = ref.current
    if (!element) return
    const observer = new ResizeObserver(() => onSize(node.id, element.offsetWidth, element.offsetHeight))
    observer.observe(element)
    return () => observer.disconnect()
  }, [node.id, onSize])
  return (
    <div
      ref={ref}
      data-node-id={node.id}
      data-kind={node.kind}
      data-selected={selected}
      data-roadmap-role={roadmapRole || undefined}
      className={`domain-node domain-${node.kind} ${selected ? 'is-selected' : ''} ${node.locked ? 'is-locked' : ''}`}
      style={{
        left: node.x,
        top: node.y,
        width: node.width,
        ...(node.kind === 'group'
          ? { height: node.height }
          : node.kind === 'text' ? { minHeight: node.height } : {}),
      }}
      onPointerDown={(e) => onStart(e, node)}
    >
      {children}
      {node.locked && <LockKeyhole className="domain-lock-indicator" size={12} aria-label={tr('已锁定')} />}
      {node.kind === 'group' && !node.locked && (
        <button
          className="domain-resize"
          disabled={readOnly}
          aria-label={tr('调整分组大小')}
          onPointerDown={(e) => onStart(e, node, true)}
        />
      )}
    </div>
  )
}

function RoadmapKeywordField({
  keywords,
  disabled,
  onChange,
}: {
  keywords: string[]
  disabled: boolean
  onChange: (keywords: string[]) => void
}) {
  useLocale()
  const [draft, setDraft] = useState('')
  const commit = (raw: string) => {
    const next = [...keywords]
    const seen = new Set(next.map(keyword => keyword.toLocaleLowerCase()))
    for (const value of raw.split(/[,，;；\n]+/)) {
      const keyword = value.trim().replace(/\s+/g, ' ').slice(0, 80)
      const key = keyword.toLocaleLowerCase()
      if (keyword && !seen.has(key) && next.length < 6) {
        next.push(keyword)
        seen.add(key)
      }
    }
    setDraft('')
    if (next.length !== keywords.length) onChange(next)
  }
  return (
    <div className="roadmap-keywords" role="group" aria-label={tr('关注关键词')}>
      <span className="roadmap-keywords-label">{tr('关键词')}</span>
      <div className="roadmap-keywords-values">
        {keywords.map(keyword => <span className="roadmap-keyword" key={keyword}>
          {keyword}
          <button
            type="button"
            disabled={disabled}
            aria-label={tr('删除关键词：{0}', { 0: keyword })}
            onClick={() => onChange(keywords.filter(value => value !== keyword))}
          ><X size={11} /></button>
        </span>)}
        <input
          value={draft}
          disabled={disabled || keywords.length >= 6}
          maxLength={80}
          aria-label={tr('添加关注关键词')}
          placeholder={keywords.length ? tr('继续添加') : tr('添加关注方向')}
          title={tr('输入关键词后按回车；最多添加 6 个。')}
          onChange={event => setDraft(event.target.value)}
          onBlur={() => commit(draft)}
          onKeyDown={event => {
            if (event.key === 'Enter' || event.key === ',' || event.key === '，' || event.key === ';') {
              event.preventDefault()
              commit(draft)
            } else if (event.key === 'Backspace' && !draft && keywords.length) {
              onChange(keywords.slice(0, -1))
            } else if (event.key === 'Escape') {
              setDraft('')
            }
          }}
        />
      </div>
    </div>
  )
}

export default function DomainPage({
  active,
  onOpenReview,
  mode = 'domain',
}: {
  active: boolean
  onOpenReview: (paperId: string) => void
  mode?: 'domain' | 'roadmap'
}) {
  useLocale()
  const isRoadmap = mode === 'roadmap'
  const api = isRoadmap ? roadmapApi : domainApi
  const pageTitle = isRoadmap ? '识途' : '索骥'
  const {
    bodyRef: domainBodyRef, width: inspectorWidth, min: inspectorMin, max: inspectorMax,
    resizing: inspectorResizing, onPointerDown: beginInspectorResize,
    onPointerMove: moveInspectorResize, onPointerUp: finishInspectorResize,
    endDrag: endInspectorResize, reset: resetInspectorWidth, onKeyDown: resizeInspectorWithKey,
    collapsed: inspectorCollapsed, toggleCollapsed: toggleInspector, expand: expandInspector,
  } = useDomainInspectorResize(mode)
  const workspace = useDomainWorkspace(api)
  const agent = useDomainAgent(api.runs)
  const refreshWorkspace = workspace.refresh
  const { state, update, checkpoint } = workspace
  const boardIndex = state?.boards.findIndex((b) => b.id === state.activeId) ?? -1
  const liveBoard = state?.boards[boardIndex] ?? null
  const run = agent.runs.find(r => r.boardId === liveBoard?.id)
  const [layoutBusy, setLayoutBusy] = useState(false)
  const [layoutMenu, setLayoutMenu] = useState(false)
  const readOnly = !agent.ready || agent.busy || layoutBusy || run?.status === 'pending' || run?.status === 'preview'
  const board = liveBoard && run?.status === 'preview' && run.proposal
    ? { ...run.proposal.board, name: liveBoard.name, category: liveBoard.category, viewport: liveBoard.viewport }
    : liveBoard
  const protect = workspace.protect
  useEffect(() => {
    protect(agent.ready ? agent.runs.filter(r => r.status === 'pending' || r.status === 'preview').map(r => r.boardId) : null)
  }, [agent.ready, agent.runs, protect])
  const loaded = state !== null
  const [papers, setPapers] = useState<DomainPaper[]>([])
  const [paperError, setPaperError] = useState(false)
  const [papersLoading, setPapersLoading] = useState(true)
  const [actionError, setActionError] = useState('')
  const [taxonomyBusy, setTaxonomyBusy] = useState(false)
  const [taxonomyError, setTaxonomyError] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null)
  const [tool, setTool] = useState<'select' | 'hand'>('select')
  const [roadmapView, setRoadmapView] = useState<'canvas' | 'report'>('canvas')
  const showReport = isRoadmap && roadmapView === 'report'
  const canvasActive = active && !showReport
  const [linkSource, setLinkSource] = useState<string | null>(null)
  const [popover, setPopover] = useState<string | null>(null)
  const [tab, setTab] = useState<ReviewField>('core_contribution')
  const [importOpen, setImportOpen] = useState(false)
  const [importSelected, setImportSelected] = useState<Set<string>>(new Set())
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('')
  const [search, setSearch] = useState('')
  const [nameDialog, setNameDialog] = useState<'new' | 'rename' | null>(null)
  const [researchDialog, setResearchDialog] = useState(false)
  const [nameDraft, setNameDraft] = useState('')
  const [rubber, setRubber] = useState<{ start: Point; end: Point } | null>(null)
  const [sizes, setSizes] = useState<Record<string, { width: number; height: number }>>({})
  const [canvasSize, setCanvasSize] = useState({ width: 1000, height: 700 })
  const canvas = useRef<HTMLDivElement>(null)
  const exportRunning = useRef(false)
  const [exportBusy, setExportBusy] = useState(false)
  const [exportNotice, setExportNotice] = useState('')
  const [exportError, setExportError] = useState('')
  const gesture = useRef<Gesture | null>(null)
  const space = useRef(false)
  const loadSequence = useRef(0)
  const fittedPreviewRuns = useRef(new Set<string>())
  const fitAfterLayout = useRef(false)
  const paperMap = new Map(papers.map((p) => [p.id, p]))
  const patchBoard = useCallback(
    (fn: (b: DomainBoard) => DomainBoard, record = true) => {
      update((s) => ({ ...s, boards: s.boards.map((b) => (b.id === s.activeId ? fn(b) : b)) }), record)
    },
    [update],
  )
  const onSize = useCallback((key: string, width: number, height: number) => {
    if (!width || !height) return
    setSizes((s) =>
      s[key]?.width === width && s[key]?.height === height ? s : { ...s, [key]: { width, height } },
    )
  }, [])
  const refreshPapers = useCallback(async () => {
    const seq = ++loadSequence.current
    try {
      const result = await api.papers()
      if (seq === loadSequence.current) {
        setPapers(result)
        setPaperError(false)
      }
    } catch {
      if (seq === loadSequence.current) setPaperError(true)
    } finally {
      if (seq === loadSequence.current) setPapersLoading(false)
    }
  }, [api])
  const imports = useRoadmapImport(isRoadmap ? liveBoard?.id ?? null : null, active && isRoadmap, refreshPapers)
  // Preview can change candidate descriptions; discarding restores the saved ones.
  useEffect(() => {
    if (!isRoadmap || !run?.status) return
    const timer = setTimeout(() => void refreshPapers(), 0)
    return () => clearTimeout(timer)
  }, [isRoadmap, run?.id, run?.status, refreshPapers])
  useEffect(() => {
    if (!active) return
    const focus = () => {
      void refreshPapers()
      void refreshWorkspace()
    }
    const start = setTimeout(focus, 0)
    const timer = setInterval(focus, 30000)
    window.addEventListener('focus', focus)
    return () => {
      clearTimeout(start)
      clearInterval(timer)
      window.removeEventListener('focus', focus)
    }
  }, [active, refreshPapers, refreshWorkspace])
  useEffect(() => {
    const element = canvas.current
    if (!element) return
    const observer = new ResizeObserver(() => {
      if (element.clientWidth && element.clientHeight) {
        setCanvasSize({ width: element.clientWidth, height: element.clientHeight })
      }
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [active, loaded])
  useEffect(() => {
    const proposal = run?.proposal?.board
    if (!isRoadmap || !canvasActive || agent.busy || liveBoard?.nodes.length !== 0 ||
      run?.status !== 'preview' || !proposal?.nodes.length || fittedPreviewRuns.current.has(run.id)) return
    // Wait for registered paper titles so their rendered card heights are known.
    if (!paperError && proposal.nodes.some(n => n.kind === 'paper' && !papers.some(p => p.id === n.paperId))) return
    const frame = requestAnimationFrame(() => {
      const element = canvas.current
      if (!element?.clientWidth || !element.clientHeight) return
      const measured = new Map(Array.from(element.querySelectorAll<HTMLElement>('[data-node-id]'))
        .map(node => [node.dataset.nodeId, { width: node.offsetWidth, height: node.offsetHeight }]))
      const minX = Math.min(...proposal.nodes.map(n => n.x)), minY = Math.min(...proposal.nodes.map(n => n.y))
      const maxX = Math.max(...proposal.nodes.map(n => n.x + (measured.get(n.id)?.width || n.width))),
        maxY = Math.max(...proposal.nodes.map(n => n.y + (measured.get(n.id)?.height || n.height)))
      const zoom = clampZoom(Math.min((element.clientWidth - 120) / (maxX - minX),
        (element.clientHeight - 120) / (maxY - minY), 1.2), 0.05)
      fittedPreviewRuns.current.add(run.id)
      patchBoard(b => ({ ...b, viewport: { zoom,
        x: (element.clientWidth - (maxX - minX) * zoom) / 2 - minX * zoom,
        y: (element.clientHeight - (maxY - minY) * zoom) / 2 - minY * zoom,
      } }), false)
    })
    return () => cancelAnimationFrame(frame)
  }, [isRoadmap, canvasActive, agent.busy, liveBoard?.nodes.length, run, papers, paperError, canvasSize, patchBoard])
  useEffect(() => {
    if (!active || !popover) return
    const outside = (event: PointerEvent) => {
      if (!canvas.current?.contains(event.target as Node)) setPopover(null)
    }
    document.addEventListener('pointerdown', outside)
    return () => document.removeEventListener('pointerdown', outside)
  }, [active, popover])
  useEffect(() => {
    if (!active || !layoutMenu) return
    const outside = (event: PointerEvent) => {
      if (!(event.target as HTMLElement).closest('.roadmap-layout-picker')) setLayoutMenu(false)
    }
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setLayoutMenu(false)
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', keydown)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', keydown)
    }
  }, [active, layoutMenu])
  useEffect(() => {
    if (!active || (!importOpen && !nameDialog && !researchDialog)) return
    const previous = document.activeElement as HTMLElement | null
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        setImportOpen(false)
        setNameDialog(null)
        setResearchDialog(false)
      }
      if (event.key !== 'Tab') return
      const controls = [
        ...document.querySelectorAll<HTMLElement>(
          `.domain-page[data-domain-mode="${mode}"] .domain-modal :is(button,input,select):not(:disabled)`,
        ),
      ]
      const first = controls[0],
        last = controls.at(-1)
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last?.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first?.focus()
      }
    }
    document.addEventListener('keydown', keydown)
    return () => {
      document.removeEventListener('keydown', keydown)
      previous?.focus()
    }
  }, [active, mode, importOpen, nameDialog, researchDialog])

  const isLocked = (n: CanvasNode, b = board) =>
    n.locked || !!b?.nodes.find((g) => g.id === n.groupId)?.locked
  const removeSelection = () => {
    if (!board || readOnly) return
    const removed = new Set(board.nodes.filter((n) => selected.has(n.id) && !isLocked(n)).map((n) => n.id))
    if (
      removed.size > 0 &&
      removed.size === board.nodes.length &&
      !window.confirm(tr('确认删除当前画布的全部元素（共 {0} 个，含连线）？论文库中的论文不会被删除，删除后可通过撤销恢复。', {
        0: board.nodes.length + board.edges.length,
      }))
    ) return
    patchBoard((b) => ({
      ...b,
      nodes: b.nodes
        .filter((n) => !removed.has(n.id))
        .map((n) => (removed.has(n.groupId ?? '') ? { ...n, groupId: null } : n)),
      edges: b.edges.filter(
        (e) => !removed.has(e.source) && !removed.has(e.target) && !(e.id === selectedEdge && !e.locked),
      ),
    }))
    setSelected(new Set())
    setSelectedEdge(null)
    if (popover && removed.has(popover)) setPopover(null)
  }
  useEffect(() => {
    if (!canvasActive) {
      space.current = false
      return
    }
    const keydown = (event: KeyboardEvent) => {
      if (
        (event.target as HTMLElement).closest('input,textarea,select,[contenteditable="true"],[role="tablist"]') ||
        importOpen ||
        nameDialog ||
        researchDialog
      )
        return
      if (event.code === 'Space') {
        space.current = true
        event.preventDefault()
      }
      if (event.key === 'Escape') {
        setPopover(null)
        setLinkSource(null)
        setSelected(new Set())
        setSelectedEdge(null)
      }
      if (!readOnly && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
        event.preventDefault()
        if (event.shiftKey) workspace.redo()
        else workspace.undo()
      }
      if (event.key === 'Delete' || event.key === 'Backspace') {
        event.preventDefault()
        removeSelection()
      }
    }
    const keyup = (e: KeyboardEvent) => {
      if (e.code === 'Space') space.current = false
    }
    const blur = () => {
      space.current = false
    }
    window.addEventListener('keydown', keydown)
    window.addEventListener('keyup', keyup)
    window.addEventListener('blur', blur)
    return () => {
      window.removeEventListener('keydown', keydown)
      window.removeEventListener('keyup', keyup)
      window.removeEventListener('blur', blur)
    }
  })
  useEffect(() => {
    const element = canvas.current
    if (!element || !canvasActive) return
    const wheel = (event: WheelEvent) => {
      if ((event.target as HTMLElement).closest('.domain-popover')) return
      event.preventDefault()
      const rect = element.getBoundingClientRect()
      patchBoard((b) => {
        const v = b.viewport
        if (event.ctrlKey || event.metaKey) {
          const zoom = clampZoom(v.zoom * Math.exp(-event.deltaY * 0.008), isRoadmap ? 0.05 : 0.15)
          const x = event.clientX - rect.left,
            y = event.clientY - rect.top
          return {
            ...b,
            viewport: { zoom, x: x - ((x - v.x) * zoom) / v.zoom, y: y - ((y - v.y) * zoom) / v.zoom },
          }
        }
        return { ...b, viewport: { ...v, x: v.x - event.deltaX, y: v.y - event.deltaY } }
      }, false)
    }
    element.addEventListener('wheel', wheel, { passive: false })
    return () => element.removeEventListener('wheel', wheel)
  }, [canvasActive, patchBoard, loaded, isRoadmap])

  const localPoint = (e: { clientX: number; clientY: number }): Point => {
    const rect = canvas.current!.getBoundingClientRect()
    return { x: e.clientX - rect.left, y: e.clientY - rect.top }
  }
  const worldPoint = (p: Point, b: DomainBoard): Point => ({
    x: (p.x - b.viewport.x) / b.viewport.zoom,
    y: (p.y - b.viewport.y) / b.viewport.zoom,
  })
  const beginCanvas = (e: ReactPointerEvent) => {
    if (!board || (e.button !== 0 && e.button !== 1)) return
    const start = localPoint(e)
    const pan = tool === 'hand' || space.current || e.button === 1
    if (!pan) {
      setPopover(null)
      setLinkSource(null)
      setSelectedEdge(null)
      if (!e.shiftKey) setSelected(new Set())
    }
    gesture.current = { mode: pan ? 'pan' : 'select', start, board, ids: selected, additive: e.shiftKey }
    if (!pan) setRubber({ start, end: start })
    canvas.current?.setPointerCapture(e.pointerId)
  }
  const beginNode = (e: ReactPointerEvent, node: CanvasNode, resize = false) => {
    if ((e.target as HTMLElement).closest('button,a,input,textarea') && !resize) {
      e.stopPropagation()
      return
    }
    if (!board) return
    e.stopPropagation()
    if (tool === 'hand' || space.current || e.button === 1) {
      beginCanvas(e)
      return
    }
    if (e.button !== 0) return
    expandInspector()
    if (!readOnly && linkSource && node.kind === 'paper') {
      connect(node.id)
      return
    }
    if (node.id !== popover) setPopover(null)
    setSelectedEdge(null)
    const next = e.shiftKey
      ? new Set(selected)
      : selected.has(node.id)
        ? new Set(selected)
        : new Set([node.id])
    if (e.shiftKey) {
      if (next.has(node.id)) next.delete(node.id)
      else next.add(node.id)
    }
    setSelected(next)
    if (readOnly || isLocked(node)) return
    const ids = new Set(board.nodes.filter((n) => next.has(n.id) && !isLocked(n)).map((n) => n.id))
    for (const n of board.nodes) if (n.groupId && ids.has(n.groupId) && !isLocked(n)) ids.add(n.id)
    checkpoint()
    gesture.current = { mode: resize ? 'resize' : 'move', start: localPoint(e), board, ids, nodeId: node.id }
    canvas.current?.setPointerCapture(e.pointerId)
  }
  const movePointer = (e: ReactPointerEvent) => {
    const g = gesture.current
    if (!g || (readOnly && (g.mode === 'move' || g.mode === 'resize'))) return
    const end = localPoint(e),
      dx = end.x - g.start.x,
      dy = end.y - g.start.y
    if (g.mode === 'select') {
      setRubber({ start: g.start, end })
      return
    }
    if (g.mode === 'pan') {
      patchBoard(
        (b) => ({
          ...b,
          viewport: { ...g.board.viewport, x: g.board.viewport.x + dx, y: g.board.viewport.y + dy },
        }),
        false,
      )
      return
    }
    patchBoard(
      (b) => ({
        ...b,
        nodes: b.nodes.map((n) => {
          const original = g.board.nodes.find((o) => o.id === n.id)!
          if (g.mode === 'resize' && n.id === g.nodeId)
            return {
              ...n,
              width: Math.min(10000, Math.max(240, original.width + dx / g.board.viewport.zoom)),
              height: Math.min(10000, Math.max(120, original.height + dy / g.board.viewport.zoom)),
            }
          return g.mode === 'move' && g.ids.has(n.id)
            ? { ...n, x: original.x + dx / g.board.viewport.zoom, y: original.y + dy / g.board.viewport.zoom }
            : n
        }),
      }),
      false,
    )
  }
  const endPointer = (e: ReactPointerEvent) => {
    const g = gesture.current
    if (!g) return
    if (g.mode === 'select') {
      const a = worldPoint(g.start, g.board),
        z = worldPoint(localPoint(e), g.board)
      const next = new Set(g.additive ? g.ids : [])
      for (const n of g.board.nodes) {
        const size = sizes[n.id] ?? n
        if (
          n.x < Math.max(a.x, z.x) &&
          n.x + size.width > Math.min(a.x, z.x) &&
          n.y < Math.max(a.y, z.y) &&
          n.y + size.height > Math.min(a.y, z.y)
        )
          next.add(n.id)
      }
      setSelected(next)
    }
    gesture.current = null
    setRubber(null)
    if (canvas.current?.hasPointerCapture(e.pointerId)) canvas.current.releasePointerCapture(e.pointerId)
  }
  const canConnect = (target: string) => {
    if (!isRoadmap || !linkSource || linkSource === target) return true
    const sourceNode = board?.nodes.find(n => n.id === linkSource)
    const targetNode = board?.nodes.find(n => n.id === target)
    return (sourceNode?.kind === 'paper' && targetNode?.kind === 'paper') ||
      (sourceNode?.roadmapRole === 'topic' && targetNode?.roadmapRole === 'branch') ||
      (sourceNode?.roadmapRole === 'branch' && targetNode?.kind === 'paper')
  }
  const connect = (target: string) => {
    if (readOnly || !canConnect(target)) return
    expandInspector()
    if (!linkSource || linkSource === target) {
      setLinkSource(target)
      return
    }
    patchBoard((b) => {
      const sourceRole = b.nodes.find(n => n.id === linkSource)?.roadmapRole
      const structure = isRoadmap && (sourceRole === 'topic' || sourceRole === 'branch')
      return {
        ...b,
        edges: [...b.edges, { id: id(), source: linkSource, target,
          label: structure ? tr(sourceRole === 'topic' ? '研究分支' : '代表论文') : '', locked: false,
          ...(structure ? { roadmapStructure: true, analysis: null } : {}),
        }],
      }
    })
    setLinkSource(null)
  }
  const zoomBy = (factor: number) =>
    patchBoard((b) => {
      const zoom = clampZoom(b.viewport.zoom * factor, isRoadmap ? 0.05 : 0.15),
        x = canvasSize.width / 2,
        y = canvasSize.height / 2
      return {
        ...b,
        viewport: {
          zoom,
          x: x - ((x - b.viewport.x) * zoom) / b.viewport.zoom,
          y: y - ((y - b.viewport.y) * zoom) / b.viewport.zoom,
        },
      }
    }, false)
  const boardNodes = board?.nodes
  const fit = useCallback(() => {
    if (!boardNodes?.length) return
    const minX = Math.min(...boardNodes.map((n) => n.x)),
      minY = Math.min(...boardNodes.map((n) => n.y))
    const maxX = Math.max(...boardNodes.map((n) => n.x + (sizes[n.id]?.width ?? n.width))),
      maxY = Math.max(...boardNodes.map((n) => n.y + (sizes[n.id]?.height ?? n.height)))
    const zoom = clampZoom(
      Math.min((canvasSize.width - 120) / (maxX - minX), (canvasSize.height - 120) / (maxY - minY), 1.2),
      isRoadmap ? 0.05 : 0.15,
    )
    patchBoard(
      (b) => ({
        ...b,
        viewport: {
          zoom,
          x: (canvasSize.width - (maxX - minX) * zoom) / 2 - minX * zoom,
          y: (canvasSize.height - (maxY - minY) * zoom) / 2 - minY * zoom,
        },
      }),
      false,
    )
  }, [boardNodes, canvasSize, isRoadmap, patchBoard, sizes])
  useEffect(() => {
    if (!fitAfterLayout.current || !board?.nodes.length) return
    fitAfterLayout.current = false
    const frame = requestAnimationFrame(fit)
    return () => cancelAnimationFrame(frame)
  }, [board?.nodes, fit])
  const focusNode = (node: CanvasNode) => {
    patchBoard(
      (b) => ({
        ...b,
        viewport: {
          zoom: 1,
          x: canvasSize.width / 2 - node.x - node.width / 2,
          y: canvasSize.height / 2 - node.y - (sizes[node.id]?.height ?? node.height) / 2,
        },
      }),
      false,
    )
    setSelected(new Set([node.id]))
    setSearch('')
  }
  const addElement = (kind: 'text' | 'group') => {
    if (!board) return
    expandInspector()
    const p = worldPoint({ x: canvasSize.width / 2 - 140, y: canvasSize.height / 2 - 80 }, board)
    const node = newNode(kind, p.x, p.y)
    node.text = tr(kind === 'text' ? '在右侧编辑文字' : '新分组')
    const members =
      kind === 'group'
        ? board.nodes.filter((n) => selected.has(n.id) && n.kind !== 'group' && !isLocked(n))
        : []
    if (members.length) {
      node.x = Math.min(...members.map((n) => n.x)) - 30
      node.y = Math.min(...members.map((n) => n.y)) - 60
      node.width = Math.max(...members.map((n) => n.x + n.width)) - node.x + 30
      node.height = Math.max(...members.map((n) => n.y + (sizes[n.id]?.height ?? n.height))) - node.y + 30
    }
    patchBoard((b) => ({
      ...b,
      nodes: [
        ...b.nodes.map((n) => (members.some((m) => m.id === n.id) ? { ...n, groupId: node.id } : n)),
        node,
      ],
    }))
    setSelected(new Set([node.id]))
    setSelectedEdge(null)
  }
  const importPapers = () => {
    if (!board || paperError || papersLoading || readOnly) return
    let added: CanvasNode[] = []
    // Resolve identity against the synchronous workspace state, so a second
    // click cannot append duplicates before React has rendered the first one.
    update((current) => {
      const b = current.boards.find(candidate => candidate.id === current.activeId)
      if (!b || b.id !== board.id) return current
      const incoming = availableImportPapers(papers, b).filter(p => importSelected.has(p.id))
      if (!incoming.length) return current
      const initial = !b.nodes.some((n) => n.kind === 'paper')
      if (initial) incoming.sort((a, b) => (parseInt(a.year) || 9999) - (parseInt(b.year) || 9999))
      const anchor = worldPoint({ x: 60, y: 60 }, b)
      const columns = Math.max(
        1,
        Math.min(4, incoming.length, Math.floor((canvasSize.width - 120) / (360 * b.viewport.zoom))),
      )
      const rowHeight = Math.max(300, ...incoming.map((p) => 130 + Math.ceil(p.title.length / 22) * 23))
      const width = columns * 360 - 80,
        height = Math.ceil(incoming.length / columns) * rowHeight
      const position = freePosition(
        { ...b, nodes: b.nodes.map((n) => ({ ...n, height: sizes[n.id]?.height ?? n.height })) },
        width,
        height,
        anchor.x,
        anchor.y,
      )
      added = incoming.map((paper, index) => ({
        ...newNode(
          'paper',
          position.x + (index % columns) * 360,
          position.y + Math.floor(index / columns) * rowHeight,
        ),
        paperId: paper.id,
      }))
      // Reveal the incoming batch without moving any existing element.
      const offscreen = position.y * b.viewport.zoom + b.viewport.y + 160 > canvasSize.height - 60
      return {
        ...current,
        boards: current.boards.map(candidate => candidate.id === b.id ? {
          ...b,
          nodes: [...b.nodes, ...added],
          viewport: offscreen
            ? { ...b.viewport, x: 80 - position.x * b.viewport.zoom, y: 60 - position.y * b.viewport.zoom }
            : b.viewport,
        } : candidate),
      }
    })
    if (!added.length) return
    setSelected(new Set(added.map((n) => n.id)))
    setSelectedEdge(null)
    setImportOpen(false)
    setImportSelected(new Set())
  }
  const selectBoard = useCallback((boardId: string) => {
    update((s) => ({ ...s, activeId: boardId }), false)
    setSelected(new Set())
    setSelectedEdge(null)
    setPopover(null)
    setLinkSource(null)
    setSearch('')
  }, [update])
  const pendingCategorySelection = useRef<string | null>(null)
  const submitName = async (e: React.FormEvent) => {
    e.preventDefault()
    const name = nameDraft.trim()
    if (!name || taxonomyBusy) return
    setTaxonomyBusy(true)
    setTaxonomyError('')
    try {
      pendingCategorySelection.current = name
      await workspace.withSaved(async () => {
        if (nameDialog === 'new') await addPaperCategory(name)
        else if (board?.category) await renamePaperCategory(board.category, name)
        else throw new Error('当前领域尚未同步，请重新加载后重试。')
      })
      await refreshWorkspace()
      setSelected(new Set())
      setPopover(null)
      setSelectedEdge(null)
      setNameDialog(null)
    } catch (error) {
      pendingCategorySelection.current = null
      setTaxonomyError(localApiErrorMessage(error, '领域大类更新失败，请重试。'))
    } finally {
      setTaxonomyBusy(false)
    }
  }
  const reorder = (offset: number) =>
    update((s) => {
      const index = s.boards.findIndex((b) => b.id === s.activeId),
        target = index + offset
      if (target < 0 || target >= s.boards.length) return s
      if (s.boards[index].category || s.boards[target].category) return s
      const boards = [...s.boards]
      ;[boards[index], boards[target]] = [boards[target], boards[index]]
      return { ...s, boards }
    })
  useEffect(() => {
    const category = pendingCategorySelection.current
    if (!category) return
    const target = state?.boards.find(item => item.category === category)
    if (!target) return
    pendingCategorySelection.current = null
    selectBoard(target.id)
  }, [selectBoard, state])
  const deleteBoard = async () => {
    if (!board?.category || board.category === '其他' || taxonomyBusy) return
    if (!window.confirm(tr(
      '删除全局领域「{0}」？该领域会从识途、索骥和其他页面移除；论文分类将回退，两个画布中的该领域内容也会删除。',
      { 0: board.name },
    ))) return
    setTaxonomyBusy(true)
    setTaxonomyError('')
    try {
      await workspace.withSaved(() => deletePaperCategory(board.category!))
      await refreshWorkspace()
      setSelected(new Set())
      setPopover(null)
      setSelectedEdge(null)
    } catch (error) {
      setTaxonomyError(localApiErrorMessage(error, '领域大类删除失败，请重试。'))
    } finally {
      setTaxonomyBusy(false)
    }
  }
  const one = board?.nodes.find((n) => selected.size === 1 && selected.has(n.id))
  const selectedPaper = one?.paperId ? paperMap.get(one.paperId) : undefined
  const edge = board?.edges.find((e) => e.id === selectedEdge)
  const popNode = board?.nodes.find((n) => n.id === popover)
  const popPaper = popNode?.paperId ? paperMap.get(popNode.paperId) : undefined
  const popX =
    board && popNode ? (popNode.x + popNode.width) * board.viewport.zoom + board.viewport.x + 12 : 0
  const popY = board && popNode ? popNode.y * board.viewport.zoom + board.viewport.y : 0
  const popLeft =
    popX + 380 > canvasSize.width && board && popNode
      ? popNode.x * board.viewport.zoom + board.viewport.x - 392
      : popX
  const popVisible =
    board &&
    popNode &&
    popX > 0 &&
    popY < canvasSize.height &&
    popY + (sizes[popNode.id]?.height ?? popNode.height) * board.viewport.zoom > 0 &&
    popNode.x * board.viewport.zoom + board.viewport.x < canvasSize.width
  const patchNode = (fn: (n: CanvasNode) => CanvasNode, record = true) =>
    patchBoard((b) => ({ ...b, nodes: b.nodes.map((n) => (selected.has(n.id) ? fn(n) : n)) }), record)
  const visibleImports = papers.filter(
    (p) =>
      (!category || p.category === category) &&
      `${p.title} ${p.year}`.toLowerCase().includes(query.toLowerCase()),
  )
  const importedIds = importedPaperIds(board)
  const selectableImports = availableImportPapers(visibleImports, board)
  const selectedImportCount = availableImportPapers(papers, board).filter(p => importSelected.has(p.id)).length
  const selectedVisibleCount = selectableImports.filter((p) => importSelected.has(p.id)).length
  const allVisibleSelected = selectableImports.length > 0 && selectedVisibleCount === selectableImports.length
  const boardPapers = [...new Set(board?.nodes.filter(n => n.kind === 'paper').map(n => n.paperId) ?? [])]
    .flatMap(paperId => { const paper = paperMap.get(paperId ?? ''); return paper ? [paper] : [] })
  const inLibraryCount = boardPapers.filter(p => p.roadmap?.libraryStatus === 'in_library').length
  const noArxivCount = boardPapers.filter(p => !p.roadmap?.arxivId).length
  const downloadableCount = new Set(boardPapers.filter(p => p.roadmap?.arxivId && p.roadmap.libraryStatus !== 'in_library').map(p => p.roadmap!.arxivId)).size
  const importResult = imports.job?.result || imports.job?.progress
  const startResearch = (relationPolicy: ResearchRelationPolicy = 'refresh') => {
    if (!board || readOnly) return
    setResearchDialog(false); setLinkSource(null); setImportOpen(false); setNameDialog(null); gesture.current = null
    void agent.act(() => workspace.withSaved(snapshot => api.start(board.id, snapshot.revision,
      Object.fromEntries(Object.entries(sizes).map(([key, value]) => [key, value.height])), relationPolicy)))
  }
  const beginResearch = () => {
    if (!board || readOnly) return
    if (isRoadmap && boardPapers.length) {
      setResearchDialog(true)
      return
    }
    startResearch()
  }
  const arrangeCanvas = (orientation: RoadmapLayoutOrientation) => {
    if (!isRoadmap || !board || readOnly) return
    setLayoutMenu(false)
    setActionError('')
    setLayoutBusy(true)
    void workspace.withSaved(snapshot => arrangeRoadmap(
      board.id,
      snapshot.revision,
      Object.fromEntries(Object.entries(sizes).map(([key, value]) => [key, value.height])),
      orientation,
    )).then(result => {
      fitAfterLayout.current = true
      workspace.install(result)
    }).catch(error => {
      setActionError(localApiErrorMessage(error, '布局整理失败，请重试。'))
    }).finally(() => setLayoutBusy(false))
  }
  const openReview = (paperId: string) => {
    const libraryId = isRoadmap ? paperMap.get(paperId)?.roadmap?.libraryPaperId : paperId
    if (!libraryId) return
    void workspace.flush()
    onOpenReview(libraryId)
  }
  const collectPaper = (paperId: string) => {
    if (!board || readOnly || imports.busy || !imports.ready) return
    setActionError('')
    void workspace.withSaved(snapshot => imports.start(board.id, snapshot.revision, [paperId])).catch(error => {
      setActionError(localApiErrorMessage(error, '论文下载启动失败，请重试。'))
    })
  }
  const statuses = {
    loading: '加载中…',
    saved: '已保存到本机',
    pending: '等待保存',
    saving: '保存中…',
    error: '保存失败，编辑仍保留在本页',
    conflict: '画布已在其他窗口更新，请重新加载',
  }
  const downloadGraph = async () => {
    if (!board?.nodes.length || !canvas.current || showReport || exportRunning.current) return
    exportRunning.current = true
    setExportBusy(true)
    setExportNotice('')
    setExportError('')
    try {
      const size = await exportDomainGraph(canvas.current, `${tr(pageTitle)}-${board.name}`)
      setExportNotice(tr('已导出 {0} × {1} 像素 · 300 DPI', { 0: size.width, 1: size.height }) +
        (size.limited ? ` · ${tr('大图已按安全像素上限缩放。')}` : ''))
    } catch (error) {
      setExportError(tr(error instanceof Error ? error.message : '图像导出失败，请重试。'))
    } finally {
      exportRunning.current = false
      setExportBusy(false)
    }
  }

  return (
    <section className={`domain-page${isRoadmap ? ' roadmap-page' : ''}`} data-domain-mode={mode} aria-label={tr(pageTitle)}>
      <header className="domain-header">
        <div>
          <span className="domain-eyebrow">{isRoadmap ? 'ROADMAP' : 'DOMAIN'}</span>
          <h1>{tr(pageTitle)}</h1>
          <p>{tr(isRoadmap ? '调研经典论文，发现技术方向的发展路线。' : '连接论文，梳理你的研究脉络。')}</p>
        </div>
        <div className={`domain-save ${workspace.status}`} role="status">
          {workspace.status === 'saving' ? (
            <Loader2 size={14} className="animate-spin" />
          ) : workspace.status === 'saved' ? (
            <Check size={14} />
          ) : null}
          <span>{tr(statuses[workspace.status])}{workspace.saveError && <> · {tr(workspace.saveError)}</>}</span>
          {workspace.status === 'error' && <button onClick={workspace.retry}>{tr('重试')}</button>}
          {workspace.status === 'conflict' && (
            <button
              onClick={() => {
                if (window.confirm(tr('放弃本页未保存修改，加载本机最新画布？'))) void workspace.reload()
              }}
            >
              {tr('重新加载')}
            </button>
          )}
        </div>
        {!showReport && <button
          className="domain-export"
          data-testid={`${mode}-download-graph`}
          disabled={!board?.nodes.length || exportBusy}
          title={tr('下载完整图；像素尺寸决定清晰度，300 DPI 用于打印。')}
          onClick={() => void downloadGraph()}
        >
          {exportBusy ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}
          {tr(exportBusy ? '正在导出…' : '下载图 PNG')}
        </button>}
        {isRoadmap ? <button
          className="domain-primary"
          disabled={!board || readOnly || imports.busy || !imports.ready || paperError || papersLoading || !downloadableCount}
          onClick={() => {
            if (!board) return
            setActionError('')
            void workspace.withSaved(snapshot => imports.start(board.id, snapshot.revision)).catch(error => {
              setActionError(localApiErrorMessage(error, '论文下载启动失败，请重试。'))
            })
          }}
        >
          {imports.busy ? <Loader2 size={16} className="spin" /> : <Download size={16} />}
          {tr(imports.busy ? '正在下载入库' : '一键下载 arXiv')}
        </button> : <button
          className="domain-primary"
          disabled={!board || readOnly}
          onClick={() => {
            setImportOpen(true)
            setImportSelected(new Set())
            setQuery('')
            setCategory('')
            void refreshPapers()
          }}
        >
          <Download size={16} />
          {tr('导入论文')}
        </button>}
      </header>
      <div className="domain-selector">
        <label className="domain-selector-field">
          <span>{tr('领域')}</span>
          <select
            value={board?.id ?? ''}
            disabled={!state?.boards.length}
            onChange={(event) => selectBoard(event.target.value)}
          >
            {!state?.boards.length && <option value="">{state ? '—' : tr('加载中…')}</option>}
            {state?.boards.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name}
              </option>
            ))}
          </select>
        </label>
        <button
          className="domain-new-board"
          aria-label={tr('新建领域')}
          disabled={!state || agent.busy || !agent.ready || taxonomyBusy}
          onClick={() => {
            setTaxonomyError('')
            setNameDraft('')
            setNameDialog('new')
          }}
        >
          <Plus size={15} />
        </button>
        {isRoadmap && board && <RoadmapKeywordField
          key={board.id}
          keywords={board.researchKeywords ?? []}
          disabled={readOnly}
          onChange={researchKeywords => patchBoard(current => ({ ...current, researchKeywords }))}
        />}
        {board && <span className="domain-imported-count" data-testid={`${mode}-imported-count`} aria-live="polite">
          {isRoadmap ? tr('共 {0} 篇 · 已入库 {1} 篇 · 非 arXiv {2} 篇', { 0: boardPapers.length, 1: inLibraryCount, 2: noArxivCount })
            : tr('已导入：{0} 张卡片', { 0: board.nodes.filter((n) => n.kind === 'paper').length })}
        </span>}
        {isRoadmap && <div className="roadmap-view-tabs" role="tablist" aria-label={tr('识途')}
          onKeyDown={event => {
            const next = event.key === 'Home' ? 'canvas' : event.key === 'End' ? 'report'
              : event.key === 'ArrowLeft' || event.key === 'ArrowRight' ? (showReport ? 'canvas' : 'report') : null
            if (!next) return
            event.preventDefault()
            setRoadmapView(next)
            event.currentTarget.querySelector<HTMLButtonElement>(`#roadmap-${next}-tab`)?.focus()
          }}>
          <button id="roadmap-canvas-tab" role="tab" aria-selected={!showReport}
            aria-controls="roadmap-canvas-panel" tabIndex={showReport ? -1 : 0}
            onClick={() => setRoadmapView('canvas')}>
            <Workflow size={15} />{tr('路线图')}
          </button>
          <button id="roadmap-report-tab" role="tab" aria-selected={showReport}
            aria-controls="roadmap-report-panel" tabIndex={showReport ? 0 : -1}
            onClick={() => setRoadmapView('report')}>
            <FileText size={15} />{tr('研报')}
          </button>
        </div>}
      </div>
      {exportError && <div className="domain-error" role="alert">{exportError}</div>}
      {exportNotice && <div className="domain-export-notice" role="status">{exportNotice}</div>}
      {paperError && (
        <div className="domain-error" role="alert">
          {tr(isRoadmap ? '调研论文加载失败，请重试。' : '论文回顾加载失败，暂时无法导入。')}
          <button onClick={() => void refreshPapers()}>{tr('重试')}</button>
        </div>
      )}
      {taxonomyError && <div className="domain-error" role="alert">{tr(taxonomyError)}</div>}
      {agent.error && <div className="domain-error" role="alert">{tr(agent.error)}<button onClick={() => {
        if (workspace.status === 'error') workspace.retry()
        agent.clearError()
        void agent.refresh()
      }}>{tr('重试')}</button></div>}
      {isRoadmap && <div className="roadmap-research-notice">
        <span>{tr('先筛选经典论文，再评估关键论文全文；按证据等级梳理关系，报告中标明全文缺口。')}</span>
        <span>{tr('下载仅保存论文到库，不自动生成回顾。')}</span>
      </div>}
      {isRoadmap && (imports.error || actionError) && <div className="domain-error" role="alert">
        {tr(actionError || imports.error)}<button onClick={() => { setActionError(''); imports.refresh() }}>{tr('重试')}</button>
      </div>}
      {isRoadmap && imports.job && <div className="roadmap-import-progress" role="status">
        <span>{imports.busy ? (imports.job.progress.label || tr('正在下载入库'))
          : imports.job.status === 'completed' ? tr('本次下载：入库 {0} 篇 · 已有 {1} 篇 · 失败 {2} 篇', {
            0: importResult?.imported ?? 0, 1: importResult?.skipped ?? 0, 2: importResult?.failed ?? 0,
          }) : (imports.job.error || tr('下载任务已停止，可重试未入库论文。'))}</span>
        {!!importResult?.failed && <span>{tr('再次点击一键下载，将跳过已入库论文并重试失败项。')}</span>}
      </div>}
      {isRoadmap && board && <RoadmapReport key={board.id} boardId={board.id} boardName={board.name}
        active={active && showReport} visible={showReport} runKey={`${run?.id ?? ''}:${run?.status ?? ''}`} />}
      <div ref={domainBodyRef} className={`domain-body${inspectorResizing ? ' is-resizing' : ''}`}
        hidden={showReport} id={isRoadmap ? 'roadmap-canvas-panel' : undefined}
        role={isRoadmap ? 'tabpanel' : undefined} aria-labelledby={isRoadmap ? 'roadmap-canvas-tab' : undefined}>
        <div
          ref={canvas}
          data-testid={`${mode}-canvas`}
          className={`domain-canvas ${tool === 'hand' ? 'hand-tool' : ''}`}
          onPointerDown={beginCanvas}
          onPointerMove={movePointer}
          onPointerUp={endPointer}
          onPointerCancel={endPointer}
          onContextMenu={(e) => e.preventDefault()}
          style={{
            backgroundPosition: `${board?.viewport.x ?? 0}px ${board?.viewport.y ?? 0}px`,
            backgroundSize: `${Math.max(isRoadmap ? 12 : 0, 24 * (board?.viewport.zoom ?? 1))}px ${Math.max(isRoadmap ? 12 : 0, 24 * (board?.viewport.zoom ?? 1))}px`,
          }}
        >
          {board && (
            <>
              <svg className="domain-edges">
                <defs>
                  <marker
                    id={`${mode}-arrow`}
                    viewBox="0 0 10 10"
                    refX="9"
                    refY="5"
                    markerWidth="7"
                    markerHeight="7"
                    orient="auto-start-reverse"
                  >
                    <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
                  </marker>
                </defs>
                <g
                  transform={`translate(${board.viewport.x} ${board.viewport.y}) scale(${board.viewport.zoom})`}
                >
                  {board.edges.map((e) => {
                    const source = board.nodes.find((n) => n.id === e.source),
                      target = board.nodes.find((n) => n.id === e.target)
                    if (!source || !target) return null
                    const geometry = isRoadmap ? roadmapEdgeGeometry(source, target,
                      sizes[source.id]?.height ?? source.height, sizes[target.id]?.height ?? target.height) : (() => {
                      const forward = target.x >= source.x
                      const sx = source.x + (forward ? source.width : 0), tx = target.x + (forward ? 0 : target.width)
                      const sy = source.y + Math.min(sizes[source.id]?.height ?? source.height, 160) / 2,
                        ty = target.y + Math.min(sizes[target.id]?.height ?? target.height, 160) / 2
                      const bend = Math.max(60, Math.abs(tx - sx) / 2) * (forward ? 1 : -1)
                      return { sx, sy, tx, ty, vertical: false, d: `M ${sx} ${sy} C ${sx + bend} ${sy}, ${tx - bend} ${ty}, ${tx} ${ty}` }
                    })()
                    const { sx, sy, tx, ty, d, vertical } = geometry
                    return (
                      <g
                        key={e.id}
                        className={`${selectedEdge === e.id ? 'selected-edge' : ''}${isRoadmap && e.roadmapStructure ? ' roadmap-structure-edge' : ''}`}
                        data-edge-id={e.id}
                        data-roadmap-structure={isRoadmap && !!e.roadmapStructure}
                        data-orientation={vertical ? 'vertical' : 'horizontal'}
                        onPointerDown={(event) => {
                          event.stopPropagation()
                          expandInspector()
                          setSelectedEdge(e.id)
                          setSelected(new Set())
                          setPopover(null)
                        }}
                      >
                        <path className="domain-edge-hit" d={d} />
                        <path className="domain-edge-line" d={d} markerEnd={`url(#${mode}-arrow)`} />
                        {e.label && (
                          <text x={(sx + tx) / 2} y={(sy + ty) / 2 - 14 / board.viewport.zoom} textAnchor="middle">
                            {e.label}
                          </text>
                        )}
                        <g transform={`translate(${(sx + tx) / 2} ${(sy + ty) / 2}) scale(${1 / board.viewport.zoom})`}>
                          <foreignObject x={-8} y={-8} width={16} height={16} className="domain-edge-lock-container">
                            <button
                              className="domain-edge-lock"
                              aria-label={tr(e.locked ? '解锁连线' : '锁定连线')}
                              title={tr(e.locked ? '解锁连线' : '锁定连线')}
                              aria-pressed={!!e.locked}
                              disabled={readOnly}
                              onPointerDown={(event) => event.stopPropagation()}
                              onKeyDown={(event) => {
                                if (event.code === 'Space' || event.key === 'Enter') event.stopPropagation()
                              }}
                              onClick={(event) => {
                                event.stopPropagation()
                                if (readOnly) return
                                expandInspector()
                                setSelectedEdge(e.id)
                                setSelected(new Set())
                                setPopover(null)
                                patchBoard((b) => ({
                                  ...b,
                                  edges: b.edges.map((item) => item.id === e.id ? { ...item, locked: !item.locked } : item),
                                }))
                              }}
                            >
                              <span
                                className="domain-edge-lock-visual"
                                style={{ transform: `scale(${Math.min(1, Math.max(0.6, board.viewport.zoom))})` }}
                              >
                                {e.locked ? <LockKeyhole size={8} /> : <UnlockKeyhole size={8} />}
                              </span>
                            </button>
                          </foreignObject>
                        </g>
                      </g>
                    )
                  })}
                </g>
              </svg>
              <div
                className="domain-world"
                style={{
                  transform: `translate(${board.viewport.x}px, ${board.viewport.y}px) scale(${board.viewport.zoom})`,
                }}
              >
                {[...board.nodes]
                  .sort((a, b) => Number(b.kind === 'group') - Number(a.kind === 'group'))
                  .map((n) => (
                    <CanvasItem
                      key={n.id}
                      node={n}
                      selected={selected.has(n.id)}
                      readOnly={readOnly}
                      onStart={beginNode}
                      onSize={onSize}
                      roadmapRole={isRoadmap ? n.roadmapRole : undefined}
                    >
                      {n.kind === 'group' ? (
                        <div className="domain-group-heading">
                          <Frame size={14} />
                          {n.text}
                        </div>
                      ) : n.kind === 'text' && isRoadmap && (n.roadmapRole === 'topic' || n.roadmapRole === 'branch') ? (
                        <>
                          <span className="roadmap-node-role">{tr(n.roadmapRole === 'topic' ? '研究主题' : '研究分支')}</span>
                          <h3 className="roadmap-node-title">{n.text}</h3>
                          <div className="domain-card-footer">
                            <button aria-label={tr('连接节点')} disabled={readOnly || !canConnect(n.id)}
                              className={linkSource === n.id ? 'connecting' : ''} onClick={() => connect(n.id)}>
                              <Link2 size={14} />{tr(linkSource && linkSource !== n.id ? '连接到这里' : '连线')}
                            </button>
                          </div>
                        </>
                      ) : n.kind === 'text' ? (
                        <div className="domain-text-content">{n.text}</div>
                      ) : (
                        <>
                          <div className="domain-paper-head">
                            <span className="roadmap-paper-year">{paperMap.get(n.paperId!)?.year || tr('年份未知')}
                              {isRoadmap && n.roadmapRole === 'milestone' && <span className="roadmap-milestone" title={tr('关键里程碑')}>
                                <Star size={12} aria-label={tr('关键里程碑')} />{tr('里程碑')}
                              </span>}
                            </span>
                            <div className="domain-paper-actions">
                              <button
                                className="domain-review-jump"
                                title={tr('打开完整回顾')}
                                aria-label={tr('打开完整回顾：{0}', { 0: paperMap.get(n.paperId!)?.title ?? '' })}
                                disabled={!n.paperId || !paperMap.has(n.paperId) || (isRoadmap && !paperMap.get(n.paperId)?.roadmap?.libraryPaperId)}
                                onKeyDown={(event) => {
                                  if (event.code === 'Space' || event.key === 'Enter') event.stopPropagation()
                                }}
                                onClick={() => {
                                  expandInspector()
                                  setSelected(new Set([n.id]))
                                  setSelectedEdge(null)
                                  setLinkSource(null)
                                  setPopover(null)
                                  openReview(n.paperId!)
                                }}
                              >
                                <ExternalLink size={14} />
                              </button>
                              <button
                                className="domain-review-plus"
                                aria-label={tr(isRoadmap ? '查看调研信息：{0}' : '查看论文回顾：{0}', {
                                  0: paperMap.get(n.paperId!)?.title ?? '',
                                })}
                                aria-expanded={popover === n.id}
                                onClick={() => {
                                  expandInspector()
                                  setPopover(popover === n.id ? null : n.id)
                                  setSelected(new Set([n.id]))
                                  setSelectedEdge(null)
                                  setTab('core_contribution')
                                }}
                              >
                                <Plus size={17} />
                              </button>
                            </div>
                          </div>
                          <h3>{paperMap.get(n.paperId!)?.title ?? tr('论文不可用')}</h3>
                          {isRoadmap && paperMap.has(n.paperId!) && <RoadmapPaperStatus paper={paperMap.get(n.paperId!)!} />}
                          {!isRoadmap && paperMap.get(n.paperId!)?.incomplete && (
                            <span className="domain-incomplete">{tr('回顾内容不完整')}</span>
                          )}
                          <div className="domain-card-footer">
                            <button
                              aria-label={tr('连接论文')}
                              disabled={readOnly || !canConnect(n.id)}
                              className={linkSource === n.id ? 'connecting' : ''}
                              onClick={() => connect(n.id)}
                            >
                              <Link2 size={14} />
                              {tr(linkSource && linkSource !== n.id ? '连接到这里' : '连线')}
                            </button>
                            {isRoadmap && sourceUrl(paperMap.get(n.paperId!)?.roadmap?.sourceUrl) && <a
                              href={sourceUrl(paperMap.get(n.paperId!)?.roadmap?.sourceUrl)!}
                              target="_blank" rel="noopener noreferrer"
                              aria-label={tr('查看论文来源：{0}', { 0: paperMap.get(n.paperId!)?.title ?? '' })}
                            ><ExternalLink size={12} />{tr('来源')}</a>}
                          </div>
                          {n.text && <div className="domain-attached-note">{n.text}</div>}
                        </>
                      )}
                    </CanvasItem>
                  ))}
              </div>
              {!board.nodes.length && (
                <div className="domain-empty">
                  <Frame size={36} />
                  <h2>{tr(isRoadmap ? '从经典论文出发，看清一个方向的来路。' : '从几篇论文开始，连接一条研究脉络。')}</h2>
                  <p>{tr(isRoadmap ? '选择技术方向，筛选约 20–30 篇经典论文，评估关键论文全文并生成路线图与调研报告。' : '导入已有论文，拖动卡片，自由连线并记录你的理解。')}</p>
                  <button
                    className="domain-primary"
                    disabled={readOnly}
                    onClick={() => {
                      if (isRoadmap) { beginResearch(); return }
                      setImportOpen(true)
                      setImportSelected(new Set())
                      setQuery('')
                      setCategory('')
                      void refreshPapers()
                    }}
                  >
                    {tr(isRoadmap ? '调研这个方向' : '导入论文')}
                  </button>
                </div>
              )}
            </>
          )}
          {!board && state && (
            <div className="domain-empty">
              <h2>{tr('新建一个领域，开始整理研究脉络。')}</h2>
              <button
                onClick={() => {
                  setNameDraft('')
                  setNameDialog('new')
                }}
              >
                {tr('新建领域')}
              </button>
            </div>
          )}
          <div className="domain-agent-bar" onPointerDown={e => e.stopPropagation()}>
            {run?.status === 'pending' ? <>
              <Loader2 size={16} className="spin" />
              <span>{run.progress.label || tr(isRoadmap ? '调研任务已排队' : '代连任务已排队')} · {tr('当前类别只读')}</span>
              <button disabled={agent.busy} onClick={() => void agent.act(() => api.discard(run.id))}>{tr('取消生成')}</button>
            </> : run?.status === 'preview' ? <>
              <span>{tr(isRoadmap ? '路线图预览' : '代连方案预览')} · {tr('当前类别只读')}</span>
              <button className="domain-primary" disabled={agent.busy} onClick={() => void agent.act(async () => {
                await workspace.withSaved(async () => {
                  const result = await api.apply(run.id)
                  workspace.install(result)
                })
                return { ...run, status: 'applied', proposal: null }
              })}>{tr('应用方案')}</button>
              <button disabled={agent.busy} onClick={() => void agent.act(() => api.discard(run.id))}>{tr('放弃方案')}</button>
            </> : <>
              {isRoadmap && board?.nodes.some(node => node.roadmapRole === 'topic') && <div className="roadmap-layout-picker">
                <button
                  aria-haspopup="menu"
                  aria-expanded={layoutMenu}
                  disabled={readOnly}
                  onClick={() => setLayoutMenu(open => !open)}
                >
                  <Network size={16} />{tr(layoutBusy ? '正在整理布局…' : '整理布局')}
                  {!layoutBusy && <ChevronDown size={14} />}
                </button>
                {layoutMenu && <div className="roadmap-layout-menu" role="menu" aria-label={tr('整理布局')}>
                  <button role="menuitem" onClick={() => arrangeCanvas('horizontal')}>
                    <ArrowDown size={18} />
                    <span className="roadmap-layout-copy">
                      <strong>{tr('横向排列')}</strong>
                      <small>{tr('研究分支横向展开，论文向下演进。')}</small>
                    </span>
                  </button>
                  <button role="menuitem" onClick={() => arrangeCanvas('vertical')}>
                    <ArrowRight size={18} />
                    <span className="roadmap-layout-copy">
                      <strong>{tr('竖直排列')}</strong>
                      <small>{tr('研究分支纵向展开，论文向右演进。')}</small>
                    </span>
                  </button>
                </div>}
              </div>}
              <button className="domain-primary"
                disabled={!board || readOnly || (!isRoadmap && new Set(board.nodes.filter(n => n.kind === 'paper').map(n => n.paperId)).size < 2)}
                onClick={beginResearch}><Sparkles size={16} />{tr(isRoadmap ? (boardPapers.length ? '重新调研' : '生成路线图') : 'agent代连')}</button>
            </>}
          </div>
          {rubber && (
            <div
              className="domain-selection-rect"
              style={{
                left: Math.min(rubber.start.x, rubber.end.x),
                top: Math.min(rubber.start.y, rubber.end.y),
                width: Math.abs(rubber.end.x - rubber.start.x),
                height: Math.abs(rubber.end.y - rubber.start.y),
              }}
            />
          )}
          <div className="domain-tools" onPointerDown={(e) => e.stopPropagation()}>
            <button
              aria-label={tr('选择与框选')}
              aria-pressed={tool === 'select'}
              onClick={() => setTool('select')}
            >
              <MousePointer2 size={18} />
            </button>
            <button
              aria-label={tr('平移画布')}
              aria-pressed={tool === 'hand'}
              onClick={() => setTool('hand')}
            >
              <Hand size={18} />
            </button>
            <hr />
            <button aria-label={tr('添加独立文本')} disabled={!board || readOnly} onClick={() => addElement('text')}>
              <Type size={18} />
            </button>
            <button aria-label={tr('创建分组')} disabled={!board || readOnly} onClick={() => addElement('group')}>
              <Frame size={18} />
            </button>
          </div>
          <div className="domain-view-tools" onPointerDown={(e) => e.stopPropagation()}>
            <button aria-label={tr('撤销')} disabled={readOnly || !workspace.history.undo} onClick={workspace.undo}>
              <Undo2 size={16} />
            </button>
            <button aria-label={tr('重做')} disabled={readOnly || !workspace.history.redo} onClick={workspace.redo}>
              <Redo2 size={16} />
            </button>
            <span className="domain-tool-divider" />
            <button aria-label={tr('缩小')} disabled={!board} onClick={() => zoomBy(0.8)}>
              <ZoomOut size={16} />
            </button>
            <span>{Math.round((board?.viewport.zoom ?? 1) * 100)}%</span>
            <button aria-label={tr('放大')} disabled={!board} onClick={() => zoomBy(1.25)}>
              <ZoomIn size={16} />
            </button>
            <button aria-label={tr('适应画布')} disabled={!board?.nodes.length} onClick={fit}>
              <Maximize size={16} />
            </button>
          </div>
          {linkSource && (
            <div className="domain-link-hint" onPointerDown={(e) => e.stopPropagation()}>
              {tr('点击另一篇论文完成连线')}
              <button onClick={() => setLinkSource(null)}>
                <X size={14} />
              </button>
            </div>
          )}
          {popNode && popVisible && (
            <div
              className="domain-popover"
              data-testid={`${mode}-review-popover`}
              role="dialog"
              aria-label={tr(isRoadmap ? '调研信息' : '论文回顾')}
              style={{
                left: Math.max(8, Math.min(popLeft, canvasSize.width - 392)),
                top: Math.max(8, Math.min(popY, canvasSize.height - 340)),
              }}
              onPointerDown={(e) => e.stopPropagation()}
            >
              <div className="domain-popover-heading">
                <span>{tr(isRoadmap ? '调研信息' : '论文回顾')}</span>
                <button aria-label={tr('关闭')} onClick={() => setPopover(null)}>
                  <X size={16} />
                </button>
              </div>
              {isRoadmap ? popPaper && <RoadmapPaperDetails paper={popPaper} onOpenReview={onOpenReview}
                onCollect={collectPaper} collectDisabled={readOnly || imports.busy || !imports.ready} /> : <>
              <div role="tablist" className="domain-review-tabs">
                {FIELDS.map(([key, label]) => (
                  <button key={key} role="tab" aria-selected={tab === key} onClick={() => setTab(key)}>
                    {tr(label)}
                  </button>
                ))}
              </div>
              <div className="domain-review-content">
                {paperError ? tr('论文回顾加载失败，暂时无法导入。') : (popPaper?.sections[tab] ?? '')}
              </div>
              {popPaper?.incomplete && <small>{tr('回顾内容不完整')}</small>}
              <button
                className="domain-full-review"
                disabled={!popPaper}
                onClick={() => {
                  openReview(popNode.paperId!)
                }}
              >
                {tr('打开完整回顾')} ↗
              </button>
              </>}
            </div>
          )}
        </div>
        <button
          className="domain-inspector-dock-toggle"
          style={{ right: inspectorCollapsed ? 10 : Math.max(0, inspectorWidth - 12) }}
          aria-label={inspectorCollapsed ? tr('展开研究工作区') : tr('收起研究工作区')}
          title={inspectorCollapsed ? tr('展开研究工作区') : tr('收起研究工作区')}
          aria-expanded={!inspectorCollapsed}
          aria-controls={`${mode}-inspector`}
          onClick={toggleInspector}
        >
          {inspectorCollapsed ? <ChevronLeft size={17} /> : <ChevronRight size={17} />}
        </button>
        <div
          className="domain-pane-divider"
          hidden={inspectorCollapsed}
          role="separator"
          aria-orientation="vertical"
          aria-label={tr('调整研究工作区宽度')}
          aria-valuemin={inspectorMin}
          aria-valuemax={inspectorMax}
          aria-valuenow={Math.round(inspectorWidth)}
          tabIndex={0}
          title={tr('拖动调整宽度，双击恢复默认')}
          onPointerDown={beginInspectorResize}
          onPointerMove={moveInspectorResize}
          onPointerUp={finishInspectorResize}
          onPointerCancel={endInspectorResize}
          onLostPointerCapture={endInspectorResize}
          onKeyDown={resizeInspectorWithKey}
          onDoubleClick={resetInspectorWidth}
        />
        <aside id={`${mode}-inspector`} className="domain-inspector" hidden={inspectorCollapsed} style={{ width: inspectorWidth }}>
          {run?.status === 'failed' && <div className="domain-error" role="alert">{run.error}</div>}
          {run?.status === 'preview' && run.proposal && <section className="domain-agent-summary">
            <h2>{tr('方案概览')}</h2>
            <p>{tr('新增 {0} 条 · 替换 {1} 条 · 保护 {2} 条', { 0: run.proposal.added, 1: run.proposal.removed, 2: run.proposal.protected })}</p>
            <p>{tr(isRoadmap ? '选中连线查看理由与证据；应用后可撤销画布调整。' : '选中连线查看理由与证据；应用后可整体撤销。')}</p>
            {run.proposal.warnings.length > 0 && <details><summary>{tr('材料提示')}</summary>{run.proposal.warnings.map((w, i) => <p key={i}>{paperMap.get(w.paperId)?.title}：{w.message}</p>)}</details>}
            {run.proposal.unconnected.length > 0 && <details><summary>{tr('未连接论文')}</summary>{run.proposal.unconnected.map(p => <p key={p.paperId}>{p.title}</p>)}</details>}
            {run.proposal.rejected > 0 && <p>{tr('已过滤 {0} 条无有效证据的建议', { 0: run.proposal.rejected })}</p>}
          </section>}
          {isRoadmap && selectedPaper && <RoadmapPaperDetails paper={selectedPaper} onOpenReview={onOpenReview}
            onCollect={collectPaper} collectDisabled={readOnly || imports.busy || !imports.ready} />}
          {edge?.analysis && <DomainEdgeEvidence key={edge.id} analysis={edge.analysis} paperMap={paperMap} onOpenReview={openReview} roadmap={isRoadmap} />}
          {isRoadmap && edge?.roadmapStructure && <section className="domain-edge-evidence roadmap-structure-explanation">
            <h2>{tr('结构连线')}</h2>
            <p>{tr('用于组织主题、分支与代表论文，不表示论文之间的证据关系。')}</p>
          </section>}
          <label className="domain-search">
            <Search size={15} />
            <input
              aria-label={tr('搜索画布中的论文')}
              placeholder={tr('搜索并定位论文')}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
          {search && (
            <div className="domain-search-results">
              {board?.nodes
                .filter(
                  (n) =>
                    n.kind === 'paper' &&
                    `${paperMap.get(n.paperId!)?.title ?? ''} ${paperMap.get(n.paperId!)?.year ?? ''}`
                      .toLowerCase()
                      .includes(search.toLowerCase()),
                )
                .map((n) => (
                  <button key={n.id} onClick={() => focusNode(n)}>
                    {paperMap.get(n.paperId!)?.title}
                    <small>{board.nodes.find((g) => g.id === n.groupId)?.text || tr('未分组')}</small>
                  </button>
                ))}
            </div>
          )}
          <fieldset disabled={readOnly} className="domain-edit-fields">
          <h2>
            {tr(
              isRoadmap && one?.roadmapRole === 'topic' ? '编辑主题'
                : isRoadmap && one?.roadmapRole === 'branch' ? '编辑分支'
                : one?.kind === 'group'
                ? '编辑分组'
                : one?.kind === 'text'
                  ? '编辑文字'
                  : one
                    ? '卡片备注'
                    : edge
                      ? '连线说明'
                      : '研究工作区',
            )}
          </h2>
          {one && (
            <>
              <textarea
                maxLength={50000}
                aria-label={tr(
                  isRoadmap && one.roadmapRole === 'topic' ? '主题名称'
                    : isRoadmap && one.roadmapRole === 'branch' ? '分支名称'
                    : one.kind === 'group' ? '分组名称' : one.kind === 'text' ? '独立文本' : '卡片备注',
                )}
                disabled={isLocked(one)}
                value={one.text}
                placeholder={tr('记录你的理解…')}
                onFocus={checkpoint}
                onChange={(e) => patchNode((n) => ({ ...n, text: e.target.value }), false)}
              />
              {one.kind === 'paper' && (
                <p className="domain-help">{tr('备注仅属于当前引用，随卡片一起移动。')}</p>
              )}
            </>
          )}
          {edge && (
            <>
              <textarea
                maxLength={2000}
                aria-label={tr('连线说明')}
                disabled={edge.locked}
                value={edge.label}
                placeholder={tr('描述这两篇论文的关系…')}
                onFocus={checkpoint}
                onChange={(event) =>
                  patchBoard(
                    (b) => ({
                      ...b,
                      edges: b.edges.map((e) => (e.id === edge.id ? { ...e, label: event.target.value } : e)),
                    }),
                    false,
                  )
                }
              />
              <div className="domain-presets">
                {(isRoadmap ? edge.roadmapStructure ? ['研究分支', '代表论文'] : ['演进', '分化', '突破推动', '融合', '待验证']
                  : ['改进', '基于', '对比', '待验证']).map((label) => (
                  <button
                    key={label}
                    disabled={edge.locked}
                    onClick={() =>
                      patchBoard((b) => ({
                        ...b,
                        edges: b.edges.map((e) => (e.id === edge.id ? { ...e, label: tr(label) } : e)),
                      }))
                    }
                  >
                    {tr(label)}
                  </button>
                ))}
              </div>
            </>
          )}
          {selected.size > 0 && (
            <>
              {board?.nodes.some((n) => selected.has(n.id) && n.kind !== 'group') && (
                <label className="domain-field">
                  {tr('所属分组')}
                  <select
                    aria-label={tr('所属分组')}
                    value={one?.groupId ?? ''}
                    onChange={(e) =>
                      patchNode((n) =>
                        n.kind !== 'group' && !isLocked(n) ? { ...n, groupId: e.target.value || null } : n,
                      )
                    }
                  >
                    <option value="">{tr('未分组')}</option>
                    {board.nodes
                      .filter((n) => n.kind === 'group' && !n.locked)
                      .map((n) => (
                        <option key={n.id} value={n.id}>
                          {n.text}
                        </option>
                      ))}
                  </select>
                </label>
              )}
              <button
                className="domain-inspector-action"
                onClick={() =>
                  patchNode((n) => ({
                    ...n,
                    locked: one
                      ? !one.locked
                      : !board?.nodes.filter((n) => selected.has(n.id)).every((n) => n.locked),
                  }))
                }
              >
                {one?.locked ? <UnlockKeyhole size={14} /> : <LockKeyhole size={14} />}
                {tr(one?.locked ? '解锁' : '切换锁定')}
              </button>
            </>
          )}
          {(selected.size > 0 || edge) && (
            <button className="domain-inspector-action domain-danger" onClick={removeSelection}>
              <Trash2 size={14} />
              {tr(one?.kind === 'group' ? '删除分组框，保留内容' : '移除选中元素')}
            </button>
          )}
          {!one && !edge && !selected.size && (
            <p className="domain-help">
              {tr('选择论文添加备注，选择连线描述关系。按住空格拖动可平移画布，Shift 点击可多选。')}
            </p>
          )}
          {selected.size > 1 && (
            <p className="domain-help">{tr('已选择 {0} 个元素', { 0: selected.size })}</p>
          )}
          <div className="domain-inspector-bottom">
            <h3>{tr('领域设置')}</h3>
            {board?.category && <p>{tr('全局大类；这里的修改会同步到所有页面。')}</p>}
            <button
              disabled={!board?.category || board.category === '其他' || taxonomyBusy || readOnly}
              onClick={() => {
                setTaxonomyError('')
                setNameDraft(board?.name ?? '')
                setNameDialog('rename')
              }}
            >
              {tr('重命名领域')}
            </button>
            <div className="domain-reorder">
              <button
                aria-label={tr('领域前移')}
                disabled={
                  !board || !!board.category || boardIndex === 0 ||
                  !!state?.boards[boardIndex - 1]?.category
                }
                onClick={() => reorder(-1)}
              >
                <ArrowUp size={14} />
                {tr('前移')}
              </button>
              <button
                aria-label={tr('领域后移')}
                disabled={!board || !!board.category || state?.boards.at(-1)?.id === board.id}
                onClick={() => reorder(1)}
              >
                <ArrowDown size={14} />
                {tr('后移')}
              </button>
            </div>
            <button
              className="domain-danger"
              disabled={!board?.category || board.category === '其他' || taxonomyBusy || readOnly}
              onClick={() => void deleteBoard()}
            >
              {tr('删除领域')}
            </button>
            <p>{tr('全局同步 · 仅保存在本机')}</p>
          </div>
          </fieldset>
        </aside>
      </div>
      {importOpen && (
        <div className="domain-modal-backdrop" onClick={() => setImportOpen(false)}>
          <section
            role="dialog"
            aria-modal="true"
            aria-label={tr('导入论文')}
            className="domain-modal domain-import"
            onClick={(e) => e.stopPropagation()}
          >
            <header>
              <div>
                <h2>{tr('导入论文')}</h2>
                <p>{tr('从论文库引用到当前领域画布；已导入的论文不会重复添加。')}</p>
              </div>
              <button aria-label={tr('关闭')} onClick={() => setImportOpen(false)}>
                <X size={18} />
              </button>
            </header>
            <div className="domain-import-filters">
              <input
                autoFocus
                aria-label={tr('搜索论文库')}
                placeholder={tr('搜索标题或年份')}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              <select
                aria-label={tr('筛选分类')}
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              >
                <option value="">{tr('全部分类')}</option>
                {[...new Set(papers.map((p) => p.category))].sort().map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </select>
            </div>
            <div className="domain-import-selection">
              <label>
                <input
                  type="checkbox"
                  checked={allVisibleSelected}
                  ref={(input) => {
                    if (input) input.indeterminate = selectedVisibleCount > 0 && !allVisibleSelected
                  }}
                  disabled={papersLoading || paperError || readOnly || selectableImports.length === 0}
                  onChange={(event) => {
                    const checked = event.target.checked
                    setImportSelected((selected) => {
                      const next = new Set(selected)
                      selectableImports.forEach((p) => {
                        if (checked) next.add(p.id)
                        else next.delete(p.id)
                      })
                      return next
                    })
                  }}
                />
                {tr('全选')}
              </label>
              <span>{tr('仅选择当前筛选结果中可导入的论文')}</span>
            </div>
            <div className="domain-import-list">
              {papersLoading ? (
                <p>{tr('加载中…')}</p>
              ) : paperError ? (
                <p>{tr('论文回顾加载失败，暂时无法导入。')}</p>
              ) : visibleImports.length === 0 ? (
                <p>{tr('没有匹配的论文')}</p>
              ) : (
                visibleImports.map((p) => {
                  const imported = importedIds.has(p.id)
                  return (
                    <label key={p.id} data-imported={imported} className={imported ? 'imported' : !p.eligible ? 'ineligible' : ''}>
                      <input
                        type="checkbox"
                        disabled={imported || !p.eligible || readOnly || papersLoading || paperError}
                        checked={!imported && p.eligible && importSelected.has(p.id)}
                        onChange={(e) =>
                          setImportSelected((s) => {
                            const next = new Set(s)
                            if (e.target.checked) next.add(p.id)
                            else next.delete(p.id)
                            return next
                          })
                        }
                      />
                      <div>
                        <strong>{p.title}</strong>
                        <small>
                          {p.year || tr('年份未知')} · {p.category}
                          {' · '}<span className="domain-import-status">{tr(imported ? '已导入' : '未导入')}</span>
                        </small>
                        {!p.eligible && (
                          <small className="domain-import-reason">
                            {tr(
                              p.reason === 'not_reviewed' ? '尚未生成论文回顾' : '回顾无法解析或四项内容全空',
                            )}
                          </small>
                        )}
                      </div>
                    </label>
                  )
                })
              )}
            </div>
            <footer>
              <span>{tr('已选择 {0} 篇论文', { 0: selectedImportCount })}</span>
              <button
                className="domain-primary"
                disabled={!selectedImportCount || paperError || papersLoading || readOnly}
                onClick={importPapers}
              >
                {tr('导入到画布')}
              </button>
            </footer>
          </section>
        </div>
      )}
      {nameDialog && (
        <div className="domain-modal-backdrop">
          <form
            className="domain-modal domain-name-dialog"
            role="dialog"
            aria-modal="true"
            aria-label={tr(nameDialog === 'new' ? '新建领域' : '重命名领域')}
            onSubmit={(event) => void submitName(event)}
          >
            <h2>{tr(nameDialog === 'new' ? '新建领域' : '重命名领域')}</h2>
            <input
              autoFocus
              maxLength={100}
              aria-label={tr('领域名称')}
              value={nameDraft}
              onChange={(e) => setNameDraft(e.target.value)}
            />
            <footer>
              <button type="button" disabled={taxonomyBusy} onClick={() => setNameDialog(null)}>
                {tr('取消')}
              </button>
              <button className="domain-primary" disabled={!nameDraft.trim() || taxonomyBusy}>
                {taxonomyBusy ? <Loader2 size={14} className="spin" /> : null}
                {tr(taxonomyBusy ? '正在保存' : '保存')}
              </button>
            </footer>
          </form>
        </div>
      )}
      {researchDialog && (
        <div className="domain-modal-backdrop" onClick={() => setResearchDialog(false)}>
          <section
            role="dialog"
            aria-modal="true"
            aria-label={tr('重新调研')}
            className="domain-modal roadmap-research-dialog"
            onClick={(event) => event.stopPropagation()}
          >
            <header>
              <div>
                <h2>{tr('是否需要清空历史关系？')}</h2>
                <p>{tr('论文卡片、备注、文字和分组都会保留。已锁定连线始终受保护。')}</p>
              </div>
              <button aria-label={tr('关闭')} onClick={() => setResearchDialog(false)}>
                <X size={18} />
              </button>
            </header>
            <div className="roadmap-research-choices">
              <button autoFocus onClick={() => startResearch('preserve')}>
                <span className="roadmap-research-choice-icon keep" aria-hidden="true"><History size={17} /></span>
                <span className="roadmap-research-choice-copy">
                  <strong>{tr('保留历史关系')}</strong>
                  <span>{tr('保留当前论文连线，再补充本轮调研发现的新关系。')}</span>
                </span>
              </button>
              <button className="domain-danger" onClick={() => startResearch('clear')}>
                <span className="roadmap-research-choice-icon clear" aria-hidden="true"><RefreshCw size={17} /></span>
                <span className="roadmap-research-choice-copy">
                  <strong>{tr('清空并重新生成')}</strong>
                  <span>{tr('清除未锁定的旧论文连线，按本轮调研结果重新生成。')}</span>
                </span>
              </button>
            </div>
            <footer>
              <span>{tr('这项选择只影响论文之间的历史连线。')}</span>
              <button onClick={() => setResearchDialog(false)}>{tr('取消')}</button>
            </footer>
          </section>
        </div>
      )}
    </section>
  )
}
