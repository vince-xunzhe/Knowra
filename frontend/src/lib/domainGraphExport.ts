import { downloadGraphBlob, graphImageSize, inlineGraphStyles, rasterizeGraphSvg, waitForGraphFonts, type GraphImageSize } from './graphImageExport'

const PADDING = 32
const running = new WeakMap<HTMLElement, Promise<GraphImageSize>>()

/** Snapshot the rendered cards and edges, never the clipped viewport or editor chrome. */
export async function createDomainGraphImage(canvas: HTMLElement) {
  const world = canvas.querySelector<HTMLElement>('.domain-world')
  const edges = canvas.querySelector<SVGSVGElement>('.domain-edges')
  const nodes = world?.querySelectorAll<HTMLElement>('[data-node-id]')
  if (!world || !edges || !nodes?.length) throw new Error('图内容为空，请先添加节点。')
  // Bound DOM/style work before cloning, independently of the eventual raster size.
  if (nodes.length > 1000 || edges.querySelectorAll('[data-edge-id]').length > 2000 ||
      world.querySelectorAll('*').length + edges.querySelectorAll('*').length > 10000 ||
      (world.textContent?.length ?? 0) + (edges.textContent?.length ?? 0) > 1_000_000) {
    throw new Error('图内容过多，无法安全导出。请减少单个画布的节点或长文本后重试。')
  }
  const backgroundColor = getComputedStyle(canvas.closest('.domain-page') ?? canvas).backgroundColor
  const zoom = new DOMMatrix(getComputedStyle(world).transform).a || 1
  const host = document.createElement('div')
  host.className = canvas.closest('.domain-page')?.className ?? 'domain-page'
  host.style.cssText = 'position:fixed;left:-100000px;top:0;width:1px;height:1px;opacity:0;pointer-events:none;overflow:visible'
  host.setAttribute('aria-hidden', 'true')
  host.inert = true
  const copy = canvas.cloneNode(false) as HTMLElement
  const worldCopy = world.cloneNode(true) as HTMLElement
  const edgesCopy = edges.cloneNode(true) as SVGSVGElement
  copy.removeAttribute('data-testid')
  copy.style.cssText = 'position:relative;width:1px;height:1px;overflow:visible;flex:none;user-select:none'
  worldCopy.style.transform = 'none'
  edgesCopy.querySelector(':scope > g')?.removeAttribute('transform')
  edgesCopy.querySelectorAll('.domain-edge-hit, .domain-edge-lock-container').forEach(element => element.remove())
  edgesCopy.querySelectorAll('.selected-edge').forEach(element => element.classList.remove('selected-edge'))
  // The editor offsets labels in screen pixels. Export at the canonical world scale.
  edgesCopy.querySelectorAll('text').forEach(element => {
    element.setAttribute('y', String(Number(element.getAttribute('y')) + 14 / zoom - 14))
  })
  worldCopy.querySelectorAll('.is-selected').forEach(element => element.classList.remove('is-selected'))
  // Keep measured card geometry while omitting controls, handles and selection markers.
  worldCopy.querySelectorAll<HTMLElement>('button, .domain-paper-actions, .domain-lock-indicator, .domain-resize')
    .forEach(element => { element.style.visibility = 'hidden' })
  copy.append(edgesCopy, worldCopy)
  host.append(copy)
  document.body.append(host)
  try {
    await waitForGraphFonts()
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
    const include = (x: number, y: number, width: number, height: number) => {
      minX = Math.min(minX, x); minY = Math.min(minY, y)
      maxX = Math.max(maxX, x + width); maxY = Math.max(maxY, y + height)
    }
    for (const node of worldCopy.querySelectorAll<HTMLElement>('[data-node-id]')) {
      include(node.offsetLeft, node.offsetTop, Math.max(node.offsetWidth, node.scrollWidth), Math.max(node.offsetHeight, node.scrollHeight))
    }
    const edgeGroup = edgesCopy.querySelector<SVGGElement>(':scope > g')
    if (edgeGroup?.querySelector('[data-edge-id]')) {
      const bounds = edgeGroup.getBBox()
      include(bounds.x - 8, bounds.y - 8, bounds.width + 16, bounds.height + 16)
    }
    const width = Math.ceil(maxX - minX + PADDING * 2)
    const height = Math.ceil(maxY - minY + PADDING * 2)
    graphImageSize(width, height) // Reject excessive coordinates before serialization/allocation.
    const x = PADDING - minX, y = PADDING - minY
    Object.assign(copy.style, { width: `${width}px`, height: `${height}px`, overflow: 'hidden',
      backgroundColor,
      backgroundSize: '24px 24px', backgroundPosition: `${x}px ${y}px` })
    Object.assign(edgesCopy.style, { width: `${width}px`, height: `${height}px` })
    edgeGroup?.setAttribute('transform', `translate(${x} ${y})`)
    worldCopy.style.transform = `translate(${x}px, ${y}px)`
    inlineGraphStyles(copy)
    const content = new XMLSerializer().serializeToString(copy)
    if (content.length > 20_000_000) throw new Error('图内容过多，无法安全导出。请减少单个画布的节点或长文本后重试。')
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><foreignObject width="100%" height="100%">${content}</foreignObject></svg>`
    return await rasterizeGraphSvg(svg, width, height)
  } finally { host.remove() }
}

/** Multiple clicks on one canvas share a single snapshot and download. */
export function exportDomainGraph(canvas: HTMLElement, filename: string): Promise<GraphImageSize> {
  const pending = running.get(canvas)
  if (pending) return pending
  const operation = createDomainGraphImage(canvas).then(({ blob, size }) => {
    downloadGraphBlob(blob, filename)
    return size
  }).finally(() => { running.delete(canvas) })
  running.set(canvas, operation)
  return operation
}
