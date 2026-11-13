import { useEffect, type RefObject } from 'react'

type ListenerTarget = Pick<EventTarget, 'addEventListener' | 'removeEventListener'>

function isInside(root: EventTarget | null | undefined, target: EventTarget | null | undefined): boolean {
  if (root == null || target == null) return false
  if (root === target) return true
  const contains = (root as { contains?: (node: unknown) => boolean }).contains
  return typeof contains === 'function' ? contains.call(root, target) : false
}

/** Bind document-level dismiss listeners and return an unbind function. */
export function bindOutsideDismiss(options: {
  root: EventTarget | null | undefined
  onDismiss: () => void
  eventTarget?: ListenerTarget | null
  closeOnEscape?: boolean
  capture?: boolean
}): () => void {
  const eventTarget = options.eventTarget ?? (typeof document === 'undefined' ? null : document)
  if (!eventTarget) return () => {}
  const capture = options.capture !== false
  const closeOnEscape = options.closeOnEscape !== false
  const onPointerDown = (event: Event) => {
    if (isInside(options.root, event.target)) return
    options.onDismiss()
  }
  const onKeyDown = (event: Event) => {
    if ((event as KeyboardEvent).key === 'Escape') options.onDismiss()
  }
  eventTarget.addEventListener('pointerdown', onPointerDown, capture)
  if (closeOnEscape) eventTarget.addEventListener('keydown', onKeyDown)
  return () => {
    eventTarget.removeEventListener('pointerdown', onPointerDown, capture)
    if (closeOnEscape) eventTarget.removeEventListener('keydown', onKeyDown)
  }
}

/** Close an open popover when pressing outside its root, or Escape. */
export function useDismissOnOutside(
  open: boolean,
  onDismiss: () => void,
  rootRef: RefObject<HTMLElement | null>,
) {
  useEffect(() => {
    if (!open) return
    return bindOutsideDismiss({ root: rootRef.current, onDismiss })
  }, [open, onDismiss, rootRef])
}
