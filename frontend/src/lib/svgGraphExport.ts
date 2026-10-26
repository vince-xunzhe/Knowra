import { downloadGraphBlob, inlineGraphStyles, rasterizeGraphSvg, waitForGraphFonts } from './graphImageExport'

export async function exportSvgGraph(source: SVGSVGElement, filename: string) {
  if (source.querySelectorAll('*').length > 10000 || (source.textContent?.length ?? 0) > 1_000_000) {
    throw new Error('图内容过多，无法安全导出。请减少单个画布的节点或长文本后重试。')
  }
  const { x: viewX, y: viewY, width: viewWidth, height: viewHeight } = source.viewBox.baseVal
  const backgroundColor = getComputedStyle(source.closest('.roadmap-diagram') ?? source).backgroundColor
  const host = document.createElement('div')
  host.className = source.closest('.domain-page')?.className ?? 'domain-page'
  host.style.cssText = 'position:fixed;left:-100000px;top:0;width:1px;height:1px;opacity:0;pointer-events:none'
  host.setAttribute('aria-hidden', 'true')
  const copy = source.cloneNode(true) as SVGSVGElement
  copy.style.cssText = `width:${viewWidth}px;height:${viewHeight}px;max-width:none;overflow:visible`
  host.append(copy)
  document.body.append(host)
  try {
    await waitForGraphFonts()
    const content = copy.getBBox()
    const x = Math.min(viewX, content.x - 12), y = Math.min(viewY, content.y - 12)
    const width = Math.ceil(Math.max(viewX + viewWidth, content.x + content.width + 12) - x)
    const height = Math.ceil(Math.max(viewY + viewHeight, content.y + content.height + 12) - y)
    copy.setAttribute('width', String(width))
    copy.setAttribute('height', String(height))
    copy.setAttribute('viewBox', `${x} ${y} ${width} ${height}`)
    copy.style.width = `${width}px`
    copy.style.height = `${height}px`
    inlineGraphStyles(copy)
    const background = document.createElementNS('http://www.w3.org/2000/svg', 'rect')
    background.setAttribute('x', String(x))
    background.setAttribute('y', String(y))
    background.setAttribute('width', String(width))
    background.setAttribute('height', String(height))
    // Mermaid styles target every rect; the page background must not inherit node fills.
    background.style.setProperty('fill', backgroundColor, 'important')
    background.style.setProperty('stroke', 'none', 'important')
    copy.prepend(background)
    const { blob, size } = await rasterizeGraphSvg(new XMLSerializer().serializeToString(copy), width, height)
    downloadGraphBlob(blob, filename)
    return size
  } finally { host.remove() }
}
