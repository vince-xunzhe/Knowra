import { useEffect, useRef, useState } from 'react'
import type { KeyboardEvent, PointerEvent } from 'react'

const STORAGE_KEY = 'knowra.domain.inspectorWidth'
const COLLAPSED_KEY = 'knowra.domain.inspectorCollapsed'
const DEFAULT_WIDTH = 250
const DIVIDER_WIDTH = 8

export function useDomainInspectorResize() {
  const bodyRef = useRef<HTMLDivElement>(null)
  const drag = useRef<{ pointerId: number; x: number; width: number } | null>(null)
  const [resizing, setResizing] = useState(false)
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(COLLAPSED_KEY) === 'true' } catch { return false }
  })
  const [bodyWidth, setBodyWidth] = useState(1000)
  const [preferredWidth, setPreferredWidth] = useState(() => {
    try {
      const stored = Number(localStorage.getItem(STORAGE_KEY))
      return Number.isFinite(stored) && stored >= 200 ? stored : DEFAULT_WIDTH
    } catch { return DEFAULT_WIDTH }
  })
  // Reserve useful canvas space; on narrow windows both panes share the space.
  const max = Math.max(0, Math.floor(bodyWidth - Math.min(320, bodyWidth / 2) - DIVIDER_WIDTH))
  const min = Math.min(200, max)
  const clamp = (value: number) => Math.min(max, Math.max(min, value))
  const width = clamp(preferredWidth)

  useEffect(() => {
    try { localStorage.setItem(COLLAPSED_KEY, String(collapsed)) } catch { /* Session toggling still works. */ }
  }, [collapsed])
  useEffect(() => {
    const body = bodyRef.current
    if (!body) return
    const observer = new ResizeObserver(() => {
      const measuredWidth = body.getBoundingClientRect().width
      if (measuredWidth > 0) setBodyWidth(measuredWidth)
    })
    observer.observe(body)
    return () => observer.disconnect()
  }, [])
  useEffect(() => {
    // Persist the preference, not the temporary clamp used for a smaller window.
    const timer = window.setTimeout(() => {
      try { localStorage.setItem(STORAGE_KEY, String(preferredWidth)) } catch { /* Session resizing still works. */ }
    }, 150)
    return () => window.clearTimeout(timer)
  }, [preferredWidth])

  const endDrag = () => {
    drag.current = null
    setResizing(false)
  }
  return {
    bodyRef, width, min, max, resizing, collapsed,
    toggleCollapsed() {
      endDrag()
      setCollapsed(value => !value)
    },
    onPointerDown(event: PointerEvent<HTMLDivElement>) {
      if (event.button !== 0) return
      event.preventDefault()
      event.stopPropagation()
      event.currentTarget.focus()
      event.currentTarget.setPointerCapture(event.pointerId)
      drag.current = { pointerId: event.pointerId, x: event.clientX, width }
      setResizing(true)
    },
    onPointerMove(event: PointerEvent<HTMLDivElement>) {
      const start = drag.current
      if (!start || start.pointerId !== event.pointerId) return
      setPreferredWidth(clamp(start.width + start.x - event.clientX))
    },
    onPointerUp(event: PointerEvent<HTMLDivElement>) {
      if (drag.current?.pointerId !== event.pointerId) return
      endDrag()
      if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
    },
    endDrag,
    reset() { setPreferredWidth(DEFAULT_WIDTH) },
    onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
      let next: number
      switch (event.key) {
        case 'ArrowLeft': next = width + (event.shiftKey ? 50 : 20); break
        case 'ArrowRight': next = width - (event.shiftKey ? 50 : 20); break
        case 'Home': next = min; break
        case 'End': next = max; break
        default: return
      }
      event.preventDefault()
      event.stopPropagation()
      setPreferredWidth(clamp(next))
    },
  }
}
