import { useCallback, useEffect, useRef, useState } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import {
  ArrowDown,
  ArrowUp,
  Check,
  Download,
  Frame,
  Hand,
  Link2,
  Loader2,
  LockKeyhole,
  Maximize,
  MousePointer2,
  Plus,
  Redo2,
  Search,
  Trash2,
  Type,
  Undo2,
  UnlockKeyhole,
  X,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
import { t as tr } from '../i18n/catalog'
import { useLocale } from '../i18n/preferences'
import {
  loadDomainPapers,
  newNode,
  freePosition,
  type CanvasNode,
  type DomainBoard,
  type DomainPaper,
  type ReviewField,
} from '../api/domains'
import { useDomainWorkspace } from '../hooks/useDomainWorkspace'
import './DomainPage.css'

const FIELDS: [ReviewField, string][] = [
  ['core_contribution', '核心贡献'],
  ['abstract_summary', '摘要'],
  ['problem', '研究问题'],
  ['motivation', '研究动机'],
]
const id = () => crypto.randomUUID()
const clampZoom = (z: number) => Math.min(2.5, Math.max(0.15, z))
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
  children,
  onStart,
  onSize,
}: {
  node: CanvasNode
  selected: boolean
  children: React.ReactNode
  onStart: (e: ReactPointerEvent, node: CanvasNode, resize?: boolean) => void
  onSize: (id: string, width: number, height: number) => void
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
      className={`domain-node domain-${node.kind} ${selected ? 'is-selected' : ''} ${node.locked ? 'is-locked' : ''}`}
      style={{
        left: node.x,
        top: node.y,
        width: node.width,
        ...(node.kind === 'group' ? { height: node.height } : { minHeight: node.height }),
      }}
      onPointerDown={(e) => onStart(e, node)}
    >
      {children}
      {node.locked && <LockKeyhole className="domain-lock-indicator" size={12} aria-label={tr('已锁定')} />}
      {node.kind === 'group' && !node.locked && (
        <button
          className="domain-resize"
          aria-label={tr('调整分组大小')}
          onPointerDown={(e) => onStart(e, node, true)}
        />
      )}
    </div>
  )
}

export default function DomainPage({
  active,
  onOpenReview,
}: {
  active: boolean
  onOpenReview: (paperId: string) => void
}) {
  useLocale()
  const workspace = useDomainWorkspace()
  const { state, update, checkpoint } = workspace
  const board = state?.boards.find((b) => b.id === state.activeId) ?? null
  const loaded = state !== null
  const [papers, setPapers] = useState<DomainPaper[]>([])
  const [paperError, setPaperError] = useState(false)
  const [papersLoading, setPapersLoading] = useState(true)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null)
  const [tool, setTool] = useState<'select' | 'hand'>('select')
  const [linkSource, setLinkSource] = useState<string | null>(null)
  const [popover, setPopover] = useState<string | null>(null)
  const [tab, setTab] = useState<ReviewField>('core_contribution')
  const [importOpen, setImportOpen] = useState(false)
  const [importSelected, setImportSelected] = useState<Set<string>>(new Set())
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState('')
  const [search, setSearch] = useState('')
  const [nameDialog, setNameDialog] = useState<'new' | 'rename' | null>(null)
  const [nameDraft, setNameDraft] = useState('')
  const [rubber, setRubber] = useState<{ start: Point; end: Point } | null>(null)
  const [sizes, setSizes] = useState<Record<string, { width: number; height: number }>>({})
  const [canvasSize, setCanvasSize] = useState({ width: 1000, height: 700 })
  const canvas = useRef<HTMLDivElement>(null)
  const gesture = useRef<Gesture | null>(null)
  const space = useRef(false)
  const loadSequence = useRef(0)
  const paperMap = new Map(papers.map((p) => [p.id, p]))
  const patchBoard = useCallback(
    (fn: (b: DomainBoard) => DomainBoard, record = true) => {
      update((s) => ({ ...s, boards: s.boards.map((b) => (b.id === s.activeId ? fn(b) : b)) }), record)
    },
    [update],
  )
  const onSize = useCallback((key: string, width: number, height: number) => {
    setSizes((s) =>
      s[key]?.width === width && s[key]?.height === height ? s : { ...s, [key]: { width, height } },
    )
  }, [])
  const refreshPapers = useCallback(async () => {
    const seq = ++loadSequence.current
    try {
      const result = await loadDomainPapers()
      if (seq === loadSequence.current) {
        setPapers(result)
        setPaperError(false)
      }
    } catch {
      if (seq === loadSequence.current) setPaperError(true)
    } finally {
      if (seq === loadSequence.current) setPapersLoading(false)
    }
  }, [])
  useEffect(() => {
    if (!active) return
    const start = setTimeout(() => void refreshPapers(), 0)
    const timer = setInterval(() => void refreshPapers(), 30000)
    const focus = () => void refreshPapers()
    window.addEventListener('focus', focus)
    return () => {
      clearTimeout(start)
      clearInterval(timer)
      window.removeEventListener('focus', focus)
    }
  }, [active, refreshPapers])
  useEffect(() => {
    const element = canvas.current
    if (!element) return
    const observer = new ResizeObserver(() =>
      setCanvasSize({ width: element.clientWidth, height: element.clientHeight }),
    )
    observer.observe(element)
    return () => observer.disconnect()
  }, [active, loaded])
  useEffect(() => {
    if (!active || !popover) return
    const outside = (event: PointerEvent) => {
      if (!canvas.current?.contains(event.target as Node)) setPopover(null)
    }
    document.addEventListener('pointerdown', outside)
    return () => document.removeEventListener('pointerdown', outside)
  }, [active, popover])
  useEffect(() => {
    if (!importOpen && !nameDialog) return
    const previous = document.activeElement as HTMLElement | null
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        setImportOpen(false)
        setNameDialog(null)
      }
      if (event.key !== 'Tab') return
      const controls = [
        ...document.querySelectorAll<HTMLElement>(
          '.domain-modal button:not(:disabled), .domain-modal input:not(:disabled), .domain-modal select:not(:disabled)',
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
  }, [importOpen, nameDialog])

  const isLocked = (n: CanvasNode, b = board) =>
    n.locked || !!b?.nodes.find((g) => g.id === n.groupId)?.locked
  const removeSelection = () => {
    if (!board) return
    const removed = new Set(board.nodes.filter((n) => selected.has(n.id) && !isLocked(n)).map((n) => n.id))
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
    if (!active) return
    const keydown = (event: KeyboardEvent) => {
      if (
        (event.target as HTMLElement).closest('input,textarea,select,[contenteditable="true"]') ||
        importOpen ||
        nameDialog
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
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z') {
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
    if (!element || !active) return
    const wheel = (event: WheelEvent) => {
      if ((event.target as HTMLElement).closest('.domain-popover')) return
      event.preventDefault()
      const rect = element.getBoundingClientRect()
      patchBoard((b) => {
        const v = b.viewport
        if (event.ctrlKey || event.metaKey) {
          const zoom = clampZoom(v.zoom * Math.exp(-event.deltaY * 0.008))
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
  }, [active, patchBoard, loaded])

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
    if ((e.target as HTMLElement).closest('button,input,textarea') && !resize) {
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
    if (linkSource && node.kind === 'paper') {
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
    if (isLocked(node)) return
    const ids = new Set(board.nodes.filter((n) => next.has(n.id) && !isLocked(n)).map((n) => n.id))
    for (const n of board.nodes) if (n.groupId && ids.has(n.groupId) && !isLocked(n)) ids.add(n.id)
    checkpoint()
    gesture.current = { mode: resize ? 'resize' : 'move', start: localPoint(e), board, ids, nodeId: node.id }
    canvas.current?.setPointerCapture(e.pointerId)
  }
  const movePointer = (e: ReactPointerEvent) => {
    const g = gesture.current
    if (!g) return
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
  const connect = (target: string) => {
    if (!linkSource || linkSource === target) {
      setLinkSource(target)
      return
    }
    patchBoard((b) => ({
      ...b,
      edges: [...b.edges, { id: id(), source: linkSource, target, label: '', locked: false }],
    }))
    setLinkSource(null)
  }
  const zoomBy = (factor: number) =>
    patchBoard((b) => {
      const zoom = clampZoom(b.viewport.zoom * factor),
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
  const fit = () => {
    if (!board?.nodes.length) return
    const minX = Math.min(...board.nodes.map((n) => n.x)),
      minY = Math.min(...board.nodes.map((n) => n.y))
    const maxX = Math.max(...board.nodes.map((n) => n.x + (sizes[n.id]?.width ?? n.width))),
      maxY = Math.max(...board.nodes.map((n) => n.y + (sizes[n.id]?.height ?? n.height)))
    const zoom = clampZoom(
      Math.min((canvasSize.width - 120) / (maxX - minX), (canvasSize.height - 120) / (maxY - minY), 1.2),
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
  }
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
    if (!board || paperError) return
    const incoming = papers.filter((p) => importSelected.has(p.id) && p.eligible)
    const initial = !board.nodes.some((n) => n.kind === 'paper')
    if (initial) incoming.sort((a, b) => (parseInt(a.year) || 9999) - (parseInt(b.year) || 9999))
    const anchor = worldPoint({ x: 60, y: 60 }, board)
    const columns = Math.max(
      1,
      Math.min(4, incoming.length, Math.floor((canvasSize.width - 120) / (360 * board.viewport.zoom))),
    )
    const rowHeight = Math.max(300, ...incoming.map((p) => 130 + Math.ceil(p.title.length / 22) * 23))
    const width = columns * 360 - 80,
      height = Math.ceil(incoming.length / columns) * rowHeight
    const position = freePosition(
      { ...board, nodes: board.nodes.map((n) => ({ ...n, height: sizes[n.id]?.height ?? n.height })) },
      width,
      height,
      anchor.x,
      anchor.y,
    )
    const nodes = incoming.map((paper, index) => ({
      ...newNode(
        'paper',
        position.x + (index % columns) * 360,
        position.y + Math.floor(index / columns) * rowHeight,
      ),
      paperId: paper.id,
    }))
    if (!nodes.length) return
    // If the visible area is full, reveal the incoming batch without moving
    // any existing element. Large batches remain selectable as a unit.
    const offscreen = position.y * board.viewport.zoom + board.viewport.y + 160 > canvasSize.height - 60
    patchBoard((b) => ({
      ...b,
      nodes: [...b.nodes, ...nodes],
      viewport: offscreen
        ? { ...b.viewport, x: 80 - position.x * b.viewport.zoom, y: 60 - position.y * b.viewport.zoom }
        : b.viewport,
    }))
    setSelected(new Set(nodes.map((n) => n.id)))
    setSelectedEdge(null)
    setImportOpen(false)
    setImportSelected(new Set())
  }
  const selectBoard = (boardId: string) => {
    update((s) => ({ ...s, activeId: boardId }), false)
    setSelected(new Set())
    setSelectedEdge(null)
    setPopover(null)
    setLinkSource(null)
    setSearch('')
  }
  const submitName = (e: React.FormEvent) => {
    e.preventDefault()
    const name = nameDraft.trim()
    if (!name) return
    if (nameDialog === 'new') {
      const next: DomainBoard = { id: id(), name, nodes: [], edges: [], viewport: { x: 80, y: 80, zoom: 1 } }
      update((s) => ({ boards: [...s.boards, next], activeId: next.id }))
      setSelected(new Set())
      setPopover(null)
      setSelectedEdge(null)
    } else patchBoard((b) => ({ ...b, name }))
    setNameDialog(null)
  }
  const reorder = (offset: number) =>
    update((s) => {
      const index = s.boards.findIndex((b) => b.id === s.activeId),
        target = index + offset
      if (target < 0 || target >= s.boards.length) return s
      const boards = [...s.boards]
      ;[boards[index], boards[target]] = [boards[target], boards[index]]
      return { ...s, boards }
    })
  const deleteBoard = () => {
    if (
      !board ||
      !window.confirm(tr('删除领域「{0}」及其画布内容？论文库中的论文不会被删除。', { 0: board.name }))
    )
      return
    update((s) => {
      const boards = s.boards.filter((b) => b.id !== s.activeId)
      return { boards, activeId: boards[0]?.id ?? null }
    })
    setSelected(new Set())
    setPopover(null)
    setSelectedEdge(null)
  }
  const one = board?.nodes.find((n) => selected.size === 1 && selected.has(n.id))
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
  const statuses = {
    loading: '加载中…',
    saved: '已保存到本机',
    pending: '等待保存',
    saving: '保存中…',
    error: '保存失败，编辑仍保留在本页',
    conflict: '画布已在其他窗口更新，请重新加载',
  }

  return (
    <section className="domain-page" aria-label={tr('索骥')}>
      <header className="domain-header">
        <div>
          <span className="domain-eyebrow">DOMAIN</span>
          <h1>{tr('索骥')}</h1>
          <p>{tr('连接论文，梳理你的研究脉络。')}</p>
        </div>
        <div className={`domain-save ${workspace.status}`} role="status">
          {workspace.status === 'saving' ? (
            <Loader2 size={14} className="animate-spin" />
          ) : workspace.status === 'saved' ? (
            <Check size={14} />
          ) : null}
          {tr(statuses[workspace.status])}
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
        <button
          className="domain-primary"
          disabled={!board}
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
        </button>
      </header>
      <div className="domain-tabs" role="tablist" aria-label={tr('领域')}>
        {state?.boards.map((b) => (
          <button key={b.id} role="tab" aria-selected={b.id === board?.id} onClick={() => selectBoard(b.id)}>
            {b.name}
            <span>{b.nodes.filter((n) => n.kind === 'paper').length}</span>
          </button>
        ))}
        <button
          className="domain-new-tab"
          aria-label={tr('新建领域')}
          disabled={!state}
          onClick={() => {
            setNameDraft('')
            setNameDialog('new')
          }}
        >
          <Plus size={15} />
        </button>
      </div>
      {paperError && (
        <div className="domain-error" role="alert">
          {tr('论文回顾加载失败，暂时无法导入。')}
          <button onClick={() => void refreshPapers()}>{tr('重试')}</button>
        </div>
      )}
      <div className="domain-body">
        <div
          ref={canvas}
          data-testid="domain-canvas"
          className={`domain-canvas ${tool === 'hand' ? 'hand-tool' : ''}`}
          onPointerDown={beginCanvas}
          onPointerMove={movePointer}
          onPointerUp={endPointer}
          onPointerCancel={endPointer}
          onContextMenu={(e) => e.preventDefault()}
          style={{
            backgroundPosition: `${board?.viewport.x ?? 0}px ${board?.viewport.y ?? 0}px`,
            backgroundSize: `${24 * (board?.viewport.zoom ?? 1)}px ${24 * (board?.viewport.zoom ?? 1)}px`,
          }}
        >
          {board && (
            <>
              <svg className="domain-edges">
                <defs>
                  <marker
                    id="domain-arrow"
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
                    const forward = target.x >= source.x
                    const sx = source.x + (forward ? source.width : 0),
                      tx = target.x + (forward ? 0 : target.width)
                    const sy = source.y + Math.min(sizes[source.id]?.height ?? source.height, 160) / 2,
                      ty = target.y + Math.min(sizes[target.id]?.height ?? target.height, 160) / 2
                    const bend = Math.max(60, Math.abs(tx - sx) / 2) * (forward ? 1 : -1)
                    const d = `M ${sx} ${sy} C ${sx + bend} ${sy}, ${tx - bend} ${ty}, ${tx} ${ty}`
                    return (
                      <g
                        key={e.id}
                        className={selectedEdge === e.id ? 'selected-edge' : ''}
                        data-edge-id={e.id}
                        onPointerDown={(event) => {
                          event.stopPropagation()
                          setSelectedEdge(e.id)
                          setSelected(new Set())
                          setPopover(null)
                        }}
                      >
                        <path className="domain-edge-hit" d={d} />
                        <path className="domain-edge-line" d={d} markerEnd="url(#domain-arrow)" />
                        {e.label && (
                          <text x={(sx + tx) / 2} y={(sy + ty) / 2 - 12} textAnchor="middle">
                            {e.label}
                          </text>
                        )}
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
                      onStart={beginNode}
                      onSize={onSize}
                    >
                      {n.kind === 'group' ? (
                        <div className="domain-group-heading">
                          <Frame size={14} />
                          {n.text}
                        </div>
                      ) : n.kind === 'text' ? (
                        <div className="domain-text-content">{n.text}</div>
                      ) : (
                        <>
                          <div className="domain-paper-head">
                            <span>{paperMap.get(n.paperId!)?.year || tr('年份未知')}</span>
                            <button
                              className="domain-review-plus"
                              aria-label={tr('查看论文回顾：{0}', {
                                0: paperMap.get(n.paperId!)?.title ?? '',
                              })}
                              aria-expanded={popover === n.id}
                              onClick={() => {
                                setPopover(popover === n.id ? null : n.id)
                                setSelected(new Set([n.id]))
                                setSelectedEdge(null)
                                setTab('core_contribution')
                              }}
                            >
                              <Plus size={17} />
                            </button>
                          </div>
                          <h3>{paperMap.get(n.paperId!)?.title ?? tr('论文不可用')}</h3>
                          {paperMap.get(n.paperId!)?.incomplete && (
                            <span className="domain-incomplete">{tr('回顾内容不完整')}</span>
                          )}
                          <div className="domain-card-footer">
                            <button
                              aria-label={tr('连接论文')}
                              className={linkSource === n.id ? 'connecting' : ''}
                              onClick={() => connect(n.id)}
                            >
                              <Link2 size={14} />
                              {tr(linkSource && linkSource !== n.id ? '连接到这里' : '连线')}
                            </button>
                            <span>{tr('论文引用')}</span>
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
                  <h2>{tr('从几篇论文开始，连接一条研究脉络。')}</h2>
                  <p>{tr('导入已有论文，拖动卡片，自由连线并记录你的理解。')}</p>
                  <button
                    className="domain-primary"
                    onClick={() => {
                      setImportOpen(true)
                      setImportSelected(new Set())
                      setQuery('')
                      setCategory('')
                      void refreshPapers()
                    }}
                  >
                    {tr('导入论文')}
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
            <button aria-label={tr('添加独立文本')} disabled={!board} onClick={() => addElement('text')}>
              <Type size={18} />
            </button>
            <button aria-label={tr('创建分组')} disabled={!board} onClick={() => addElement('group')}>
              <Frame size={18} />
            </button>
          </div>
          <div className="domain-view-tools" onPointerDown={(e) => e.stopPropagation()}>
            <button aria-label={tr('撤销')} disabled={!workspace.history.undo} onClick={workspace.undo}>
              <Undo2 size={16} />
            </button>
            <button aria-label={tr('重做')} disabled={!workspace.history.redo} onClick={workspace.redo}>
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
              data-testid="domain-review-popover"
              role="dialog"
              aria-label={tr('论文回顾')}
              style={{
                left: Math.max(8, Math.min(popLeft, canvasSize.width - 392)),
                top: Math.max(8, Math.min(popY, canvasSize.height - 340)),
              }}
              onPointerDown={(e) => e.stopPropagation()}
            >
              <div className="domain-popover-heading">
                <span>{tr('论文回顾')}</span>
                <button aria-label={tr('关闭')} onClick={() => setPopover(null)}>
                  <X size={16} />
                </button>
              </div>
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
                  void workspace.flush()
                  onOpenReview(popNode.paperId!)
                }}
              >
                {tr('打开完整回顾')} ↗
              </button>
            </div>
          )}
        </div>
        <aside className="domain-inspector">
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
          <h2>
            {tr(
              one?.kind === 'group'
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
                  one.kind === 'group' ? '分组名称' : one.kind === 'text' ? '独立文本' : '卡片备注',
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
                {['改进', '基于', '对比', '待验证'].map((label) => (
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
          {edge && (
            <button
              className="domain-inspector-action"
              onClick={() =>
                patchBoard((b) => ({
                  ...b,
                  edges: b.edges.map((e) => (e.id === edge.id ? { ...e, locked: !e.locked } : e)),
                }))
              }
            >
              {tr(edge.locked ? '解锁' : '锁定连线')}
            </button>
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
            <button
              disabled={!board}
              onClick={() => {
                setNameDraft(board?.name ?? '')
                setNameDialog('rename')
              }}
            >
              {tr('重命名领域')}
            </button>
            <div className="domain-reorder">
              <button
                aria-label={tr('领域前移')}
                disabled={!board || state?.boards[0]?.id === board.id}
                onClick={() => reorder(-1)}
              >
                <ArrowUp size={14} />
                {tr('前移')}
              </button>
              <button
                aria-label={tr('领域后移')}
                disabled={!board || state?.boards.at(-1)?.id === board.id}
                onClick={() => reorder(1)}
              >
                <ArrowDown size={14} />
                {tr('后移')}
              </button>
            </div>
            <button className="domain-danger" disabled={!board} onClick={deleteBoard}>
              {tr('删除领域')}
            </button>
            <p>{tr('仅保存在本机')}</p>
          </div>
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
                <p>{tr('从论文库引用，保留你的画布布局。')}</p>
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
            <div className="domain-import-list">
              {papersLoading ? (
                <p>{tr('加载中…')}</p>
              ) : paperError ? (
                <p>{tr('论文回顾加载失败，暂时无法导入。')}</p>
              ) : visibleImports.length === 0 ? (
                <p>{tr('没有匹配的论文')}</p>
              ) : (
                visibleImports.map((p) => {
                  const count = board?.nodes.filter((n) => n.paperId === p.id).length ?? 0
                  return (
                    <label key={p.id} className={!p.eligible ? 'ineligible' : ''}>
                      <input
                        type="checkbox"
                        disabled={!p.eligible}
                        checked={importSelected.has(p.id)}
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
                          {count > 0 && ` · ${tr('添加另一个引用（已有 {0} 个）', { 0: count })}`}
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
              <span>{tr('已选择 {0} 篇论文', { 0: importSelected.size })}</span>
              <button
                className="domain-primary"
                disabled={!importSelected.size || paperError}
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
            onSubmit={submitName}
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
              <button type="button" onClick={() => setNameDialog(null)}>
                {tr('取消')}
              </button>
              <button className="domain-primary" disabled={!nameDraft.trim()}>
                {tr('保存')}
              </button>
            </footer>
          </form>
        </div>
      )}
    </section>
  )
}
