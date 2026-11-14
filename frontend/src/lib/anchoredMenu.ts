/** Viewport-aware placement for a portaled menu. Pure geometry, no DOM writes. */

export type AnchoredMenuPlacement = 'top' | 'bottom'

export type AnchoredMenuBox = {
  top: number
  left: number
  width: number
  height: number
}

export type AnchoredMenuPosition = {
  top: number
  bottom?: number
  left: number
  width: number
  maxHeight: number
  placement: AnchoredMenuPlacement
}

export const ANCHORED_MENU_GAP = 4
export const ANCHORED_MENU_VIEWPORT_PAD = 8
export const ANCHORED_MENU_DEFAULT_MAX_HEIGHT = 240
export const ANCHORED_MENU_MIN_HEIGHT = 96

export function computeAnchoredMenuPosition(
  trigger: AnchoredMenuBox,
  viewport: { width: number; height: number },
  options: {
    minWidth?: number
    maxWidth?: number
    preferredMaxHeight?: number
    menuHeight?: number
  } = {},
): AnchoredMenuPosition {
  const pad = ANCHORED_MENU_VIEWPORT_PAD
  const gap = ANCHORED_MENU_GAP
  const preferred = options.preferredMaxHeight ?? ANCHORED_MENU_DEFAULT_MAX_HEIGHT
  const minWidth = options.minWidth ?? trigger.width
  const maxWidth = Math.max(
    0,
    options.maxWidth ?? Math.min(viewport.width - pad * 2, Math.max(trigger.width, 320)),
  )
  const width = Math.min(Math.max(minWidth, trigger.width, 0), maxWidth || minWidth)
  let left = trigger.left
  if (left + width > viewport.width - pad) left = viewport.width - pad - width
  if (left < pad) left = Math.max(0, pad)

  const spaceBelow = viewport.height - (trigger.top + trigger.height) - pad - gap
  const spaceAbove = trigger.top - pad - gap
  const needed = Math.min(preferred, options.menuHeight ?? preferred)
  const placement: AnchoredMenuPlacement =
    spaceBelow < needed && spaceAbove > spaceBelow ? 'top' : 'bottom'
  const available = placement === 'top' ? spaceAbove : spaceBelow
  const maxHeight = Math.max(
    Math.min(ANCHORED_MENU_MIN_HEIGHT, Math.max(0, available)),
    Math.min(preferred, Math.max(0, available)),
  )
  if (placement === 'top') {
    const bottom = Math.max(0, viewport.height - (trigger.top - gap))
    return { top: trigger.top - gap - maxHeight, bottom, left, width, maxHeight, placement }
  }
  return { top: trigger.top + trigger.height + gap, left, width, maxHeight, placement }
}
