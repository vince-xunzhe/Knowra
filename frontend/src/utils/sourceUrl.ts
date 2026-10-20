/** Only web URLs may be opened from model-produced source metadata. */
export function sourceUrl(value: string | undefined) {
  try {
    const url = new URL(value || '')
    return ['https:', 'http:'].includes(url.protocol) ? url.href : null
  } catch { return null }
}
