import type { CanvasNode } from '../api/domains'

/** Prefer top/bottom ports for separate tree levels; siblings retain side ports. */
export function roadmapEdgeGeometry(source: CanvasNode, target: CanvasNode, sourceHeight: number, targetHeight: number) {
  const down = target.y >= source.y + sourceHeight + 24
  const up = source.y >= target.y + targetHeight + 24
  if (down || up) {
    const sx = source.x + source.width / 2, tx = target.x + target.width / 2
    const sy = source.y + (down ? sourceHeight : 0), ty = target.y + (down ? 0 : targetHeight)
    const bend = Math.max(40, Math.abs(ty - sy) / 2) * (down ? 1 : -1)
    return { sx, sy, tx, ty, vertical: true, d: `M ${sx} ${sy} C ${sx} ${sy + bend}, ${tx} ${ty - bend}, ${tx} ${ty}` }
  }
  const forward = target.x >= source.x
  const sx = source.x + (forward ? source.width : 0), tx = target.x + (forward ? 0 : target.width)
  const sy = source.y + sourceHeight / 2, ty = target.y + targetHeight / 2
  const bend = Math.max(60, Math.abs(tx - sx) / 2) * (forward ? 1 : -1)
  return { sx, sy, tx, ty, vertical: false, d: `M ${sx} ${sy} C ${sx + bend} ${sy}, ${tx - bend} ${ty}, ${tx} ${ty}` }
}
