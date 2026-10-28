import { useCallback, useEffect, useState } from 'react'
import type { DomainBoard, DomainState } from '../api/domains'
import { arrangeDomainLayout } from '../lib/domainLayout'

type Preview = ReturnType<typeof arrangeDomainLayout> & {
  source: DomainBoard
  original: DomainBoard
  showOriginal: boolean
}

/** A preview never enters the workspace save queue or its undo history. */
export function useDomainLayoutPreview(
  liveBoard: DomainBoard | null,
  enabled: boolean,
  update: (fn: (state: DomainState) => DomainState, record?: boolean) => void,
) {
  const [draft, setDraft] = useState<Preview | null>(null)
  // A changed source (including refresh), field switch, or hidden page invalidates
  // the preview synchronously, before a stale Apply button can be used.
  const preview = enabled && draft?.source === liveBoard ? draft : null
  // Remember invalidation immediately, even if the page is shown again before
  // effects run. The guarded reset converges on the next render.
  if (draft && !preview) setDraft(null)

  const start = (sizes: Record<string, { width: number; height: number }>, canvas: { width: number; height: number }) => {
    if (!enabled || !liveBoard || preview) return
    const result = arrangeDomainLayout(liveBoard, sizes)
    const nodes = result.board.nodes
    const minX = Math.min(...nodes.map(n => n.x)), minY = Math.min(...nodes.map(n => n.y))
    const maxX = Math.max(...nodes.map(n => n.x + (sizes[n.id]?.width ?? n.width)))
    const maxY = Math.max(...nodes.map(n => n.y + (sizes[n.id]?.height ?? n.height)))
    const zoom = Math.max(0.15, Math.min(1, (canvas.width - 160) / Math.max(1, maxX - minX),
      (canvas.height - 220) / Math.max(1, maxY - minY)))
    const viewport = { zoom,
      x: (canvas.width - (maxX - minX) * zoom) / 2 - minX * zoom,
      y: 150 + (canvas.height - 220 - (maxY - minY) * zoom) / 2 - minY * zoom,
    }
    setDraft({ ...result, board: { ...result.board, viewport }, source: liveBoard,
      original: liveBoard, showOriginal: false })
  }
  const cancel = useCallback(() => setDraft(null), [])
  useEffect(() => {
    if (!preview) return
    const keydown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      cancel()
    }
    window.addEventListener('keydown', keydown)
    return () => window.removeEventListener('keydown', keydown)
  }, [preview, cancel])
  const patchViewport = useCallback((fn: (board: DomainBoard) => DomainBoard) => {
    setDraft(current => {
      if (!enabled || current?.source !== liveBoard) return current
      const key = current.showOriginal ? 'original' : 'board'
      // Only the viewport is editable while previewing. Neither side can mutate
      // paper content, positions or relationships through another canvas control.
      return { ...current, [key]: { ...current[key], viewport: fn(current[key]).viewport } }
    })
  }, [enabled, liveBoard])
  const toggle = () => setDraft(current => current && { ...current, showOriginal: !current.showOriginal })
  const apply = () => {
    if (!preview) return
    if (preview.moved) update(state => {
      if (state.activeId !== preview.source.id) return state
      const index = state.boards.findIndex(board => board === preview.source)
      if (index < 0) return state
      return { ...state, boards: state.boards.map((board, i) => i === index ? preview.board : board) }
    })
    setDraft(null)
  }
  return { preview, board: preview ? (preview.showOriginal ? preview.original : preview.board) : null,
    start, cancel, patchViewport, toggle, apply }
}
