import { useCallback, useLayoutEffect, useState, type CSSProperties, type RefObject } from 'react'
import {
  computeAnchoredMenuPosition,
  type AnchoredMenuPlacement,
} from '../lib/anchoredMenu'

const MENU_Z_INDEX = 40

const HIDDEN_STYLE: CSSProperties = {
  position: 'fixed',
  top: 0,
  left: 0,
  visibility: 'hidden',
  zIndex: MENU_Z_INDEX,
}

export function useAnchoredMenu(
  open: boolean,
  triggerRef: RefObject<HTMLElement | null>,
  menuRef: RefObject<HTMLElement | null>,
  onTriggerHidden?: () => void,
) {
  const [style, setStyle] = useState<CSSProperties>(HIDDEN_STYLE)
  const [placement, setPlacement] = useState<AnchoredMenuPlacement>('bottom')

  const update = useCallback(() => {
    const trigger = triggerRef.current
    if (!trigger) return
    const rect = trigger.getBoundingClientRect()
    const visible = rect.bottom > 0 && rect.top < window.innerHeight && rect.right > 0 && rect.left < window.innerWidth
    if (!visible) {
      onTriggerHidden?.()
      return
    }
    const pos = computeAnchoredMenuPosition(
      { top: rect.top, left: rect.left, width: rect.width, height: rect.height },
      { width: window.innerWidth, height: window.innerHeight },
      { menuHeight: menuRef.current?.scrollHeight },
    )
    setPlacement(pos.placement)
    setStyle({
      position: 'fixed',
      top: pos.placement === 'bottom' ? pos.top : 'auto',
      bottom: pos.placement === 'top' ? pos.bottom : 'auto',
      left: pos.left,
      width: pos.width,
      maxHeight: pos.maxHeight,
      visibility: 'visible',
      zIndex: MENU_Z_INDEX,
    })
  }, [menuRef, onTriggerHidden, triggerRef])

  useLayoutEffect(() => {
    if (!open) return
    update()
    const frame = window.requestAnimationFrame(update)
    window.addEventListener('resize', update)
    window.addEventListener('scroll', update, true)
    window.visualViewport?.addEventListener('resize', update)
    window.visualViewport?.addEventListener('scroll', update)
    return () => {
      window.cancelAnimationFrame(frame)
      window.removeEventListener('resize', update)
      window.removeEventListener('scroll', update, true)
      window.visualViewport?.removeEventListener('resize', update)
      window.visualViewport?.removeEventListener('scroll', update)
    }
  }, [open, update])

  return { style, placement, update }
}
