/** Shared, dependency-free PNG export. DPI is print metadata; scale determines detail. */
export const GRAPH_EXPORT_DPI = 300
export const GRAPH_EXPORT_SCALE = GRAPH_EXPORT_DPI / 96
const MAX_EDGE = 8192
const MAX_PIXELS = 24_000_000

export interface GraphImageSize {
  width: number
  height: number
  scale: number
  limited: boolean
}

export function graphImageSize(width: number, height: number): GraphImageSize {
  if (![width, height].every(value => Number.isFinite(value) && value > 0)) {
    throw new Error('图内容为空或尺寸无效。')
  }
  const scale = Math.min(GRAPH_EXPORT_SCALE, MAX_EDGE / width, MAX_EDGE / height,
    Math.sqrt(MAX_PIXELS / (width * height)))
  // Never quietly produce a giant graph with unreadably small labels.
  if (scale < 1) throw new Error('图的范围过大，无法在安全内存内清晰导出。请收紧节点布局后重试。')
  return { width: Math.floor(width * scale), height: Math.floor(height * scale), scale,
    limited: scale < GRAPH_EXPORT_SCALE }
}

function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff
  for (const byte of bytes) {
    crc ^= byte
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0)
  }
  return (crc ^ 0xffffffff) >>> 0
}

/** Replace any existing density chunk, keeping image data byte-for-byte unchanged. */
export function withPngDensity(bytes: Uint8Array, dpi = GRAPH_EXPORT_DPI): Uint8Array<ArrayBuffer> {
  if (bytes.length < 33 || ![137, 80, 78, 71, 13, 10, 26, 10].every((byte, i) => bytes[i] === byte)) {
    throw new Error('无法读取导出的 PNG。')
  }
  const chunk = new Uint8Array(21)
  const view = new DataView(chunk.buffer)
  view.setUint32(0, 9)
  chunk.set([112, 72, 89, 115], 4) // pHYs
  const pixelsPerMetre = Math.round(dpi / 0.0254)
  view.setUint32(8, pixelsPerMetre)
  view.setUint32(12, pixelsPerMetre)
  chunk[16] = 1
  view.setUint32(17, crc32(chunk.subarray(4, 17)))
  const parts: Uint8Array[] = [bytes.subarray(0, 8)]
  const source = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  let inserted = false
  for (let offset = 8; offset < bytes.length;) {
    if (offset + 12 > bytes.length) throw new Error('导出的 PNG 数据不完整。')
    const length = source.getUint32(offset)
    const end = offset + length + 12
    if (end > bytes.length) throw new Error('导出的 PNG 数据不完整。')
    const type = String.fromCharCode(...bytes.subarray(offset + 4, offset + 8))
    if (type !== 'pHYs') parts.push(bytes.subarray(offset, end))
    if (type === 'IHDR') { parts.push(chunk); inserted = true }
    offset = end
  }
  if (!inserted) throw new Error('导出的 PNG 缺少尺寸信息。')
  const output = new Uint8Array(parts.reduce((total, part) => total + part.length, 0))
  let offset = 0
  for (const part of parts) { output.set(part, offset); offset += part.length }
  return output
}

export function downloadGraphBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${filename.replace(/[<>:"/\\|?*]/g, '-').replace(/\p{Cc}/gu, '-').slice(0, 100) || 'Knowra-graph'}.png`
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  // Give the browser time to consume the download before releasing its URL.
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export async function withGraphExportDeadline<T>(operation: Promise<T>): Promise<T> {
  let timeout: ReturnType<typeof setTimeout> | undefined
  try {
    return await Promise.race([operation, new Promise<never>((_, reject) => {
      timeout = setTimeout(() => reject(new Error('图像导出超时，请收紧节点布局后重试。')), 15000)
    })])
  } finally { clearTimeout(timeout) }
}

export async function waitForGraphFonts(): Promise<void> {
  await withGraphExportDeadline(document.fonts.ready)
}

export async function rasterizeGraphSvg(svg: string, width: number, height: number): Promise<{
  blob: Blob
  size: GraphImageSize
}> {
  const size = graphImageSize(width, height)
  const bitmap = document.createElement('canvas')
  bitmap.width = size.width
  bitmap.height = size.height
  const image = new Image()
  try {
    // A self-contained data URL allows foreignObject HTML to remain origin-clean in Chrome.
    const imageReady = new Promise<void>((resolve, reject) => {
      image.onload = () => resolve()
      image.onerror = () => reject(new Error('图片渲染失败，请重试或使用最新版浏览器。'))
    })
    image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
    await withGraphExportDeadline(imageReady)
    const context = bitmap.getContext('2d')
    if (!context) throw new Error('浏览器无法分配图片内存，请收紧节点布局后重试。')
    context.drawImage(image, 0, 0, size.width, size.height)
    const raw = await withGraphExportDeadline(new Promise<Blob>((resolve, reject) => bitmap.toBlob(blob => {
      if (blob) resolve(blob)
      else reject(new Error('PNG 编码失败，请收紧节点布局后重试。'))
    }, 'image/png')))
    const bytes = withPngDensity(new Uint8Array(await raw.arrayBuffer()))
    return { blob: new Blob([bytes], { type: 'image/png' }), size }
  } finally {
    image.onload = null
    image.onerror = null
    image.src = ''
    bitmap.width = 0
    bitmap.height = 0
  }
}

// Only visual/layout properties are needed. No external stylesheets, fonts, images,
// interaction state, or application URLs are included in the exported document.
const VISUAL_PROPERTIES = [
  'display', 'position', 'top', 'right', 'bottom', 'left', 'width', 'height', 'min-width', 'min-height',
  'max-width', 'max-height', 'box-sizing', 'margin', 'padding', 'border', 'border-top', 'border-right',
  'border-bottom', 'border-left', 'border-radius', 'box-shadow', 'color', 'background-color',
  'background-image', 'background-size', 'background-position', 'background-repeat',
  'font-family', 'font-size', 'font-weight', 'font-style', 'line-height', 'letter-spacing',
  'white-space', 'overflow-wrap', 'word-break', 'text-align', 'text-decoration', 'text-transform',
  'overflow', 'opacity', 'visibility', 'vertical-align', 'flex', 'flex-direction', 'flex-wrap',
  'justify-content', 'align-items', 'align-content', 'align-self', 'gap', 'row-gap', 'column-gap',
  'transform', 'transform-origin', 'fill', 'fill-opacity', 'stroke', 'stroke-width', 'stroke-opacity',
  'stroke-linecap', 'stroke-linejoin', 'stroke-dasharray', 'paint-order', 'text-anchor',
  'dominant-baseline', 'marker-start', 'marker-mid', 'marker-end',
]

export function inlineGraphStyles(root: Element): void {
  for (const element of [root, ...root.querySelectorAll('*')]) {
    if (!(element instanceof HTMLElement || element instanceof SVGElement)) continue
    const computed = getComputedStyle(element)
    const style = VISUAL_PROPERTIES.map(property => {
      const value = computed.getPropertyValue(property).replace(/url\(["']?[^)"']*#([^\s)"']+)["']?\)/g, 'url(#$1)')
      return `${property}:${value}`
    }).join(';')
    element.setAttribute('style', style)
    for (const name of element.getAttributeNames()) {
      if (name.startsWith('on') || ['href', 'src', 'tabindex', 'contenteditable'].includes(name)) element.removeAttribute(name)
    }
  }
}
