const UNICODE_MATH_REPLACEMENTS: ReadonlyArray<readonly [RegExp, string]> = [
  [/[～∼]/g, '\\sim '],
  [/θ/g, '\\theta '],
  [/α/g, '\\alpha '],
  [/β/g, '\\beta '],
  [/γ/g, '\\gamma '],
  [/δ/g, '\\delta '],
  [/λ/g, '\\lambda '],
  [/μ/g, '\\mu '],
  [/σ/g, '\\sigma '],
  [/π/g, '\\pi '],
  [/φ/g, '\\phi '],
  [/ϕ/g, '\\phi '],
  [/ω/g, '\\omega '],
  [/Φ/g, '\\Phi '],
  [/Ω/g, '\\Omega '],
  [/Δ/g, '\\Delta '],
  [/∑/g, '\\sum '],
  [/∏/g, '\\prod '],
  [/∫/g, '\\int '],
  [/∞/g, '\\infty '],
  [/≤/g, '\\le '],
  [/≥/g, '\\ge '],
  [/≠/g, '\\ne '],
  [/≈/g, '\\approx '],
  [/∈/g, '\\in '],
  [/∉/g, '\\notin '],
  [/∩/g, '\\cap '],
  [/∪/g, '\\cup '],
  [/∥/g, '\\| '],
  [/∇/g, '\\nabla '],
  [/×/g, '\\times '],
  [/·/g, '\\cdot '],
  [/→/g, '\\to '],
  [/←/g, '\\leftarrow '],
  [/√/g, '\\sqrt '],
  [/⊤/g, '^{\\top}'],
  [/′/g, "'"],
  [/²/g, '^2'],
  [/–/g, '-'],
  [/(?<!\\)#/g, '\\#'],
]

function stripOuterMathDelimiters(raw: string): string {
  const text = (raw || '').trim()
  if (!text) return ''
  if (text.startsWith('$$') && text.endsWith('$$')) return text.slice(2, -2).trim()
  if (text.startsWith('\\[') && text.endsWith('\\]')) return text.slice(2, -2).trim()
  if (text.startsWith('$') && text.endsWith('$')) return text.slice(1, -1).trim()
  return text
}

/**
 * Repairs conservative, recurring model-output mistakes without rewriting
 * already-valid LaTeX. The stored extraction remains untouched; this adapter
 * keeps historical records renderable while newer prompts enforce clean LaTeX.
 */
export function normalizeFormula(raw: string): string {
  let text = stripOuterMathDelimiters(raw).replace(/\u00a0/g, ' ').normalize('NFD')
  if (!text) return ''

  // NFD represents a few negated operators as a base symbol plus U+0338.
  text = text
    .replace(/=\u0338/g, '\\ne ')
    .replace(/∈\u0338/g, '\\notin ')
    .replace(/\^\s*⊤/g, '^{\\top}')

  // PDF text extraction frequently leaves combining accents detached from the
  // symbol they decorate. Convert both normal and reversed accent order.
  text = text
    .replace(/([A-Za-zͰ-Ͽ])\u0302/g, '\\hat{$1}')
    .replace(/([A-Za-zͰ-Ͽ])\u0303/g, '\\tilde{$1}')
    .replace(/([A-Za-zͰ-Ͽ])\u0304/g, '\\bar{$1}')
    .replace(/\u0302([A-Za-zͰ-Ͽ])/g, '\\hat{$1}')
    .replace(/\u0303([A-Za-zͰ-Ͽ])/g, '\\tilde{$1}')
    .replace(/\u0304([A-Za-zͰ-Ͽ])/g, '\\bar{$1}')

  // A capital Sigma followed by an indexed range is a sum operator; elsewhere
  // preserve its meaning as the Greek letter Sigma.
  text = text
    .replace(/Σ(?=\s*_)/g, '\\sum ')
    .replace(/Σ/g, '\\Sigma ')

  for (const [pattern, replacement] of UNICODE_MATH_REPLACEMENTS) {
    text = text.replace(pattern, replacement)
  }

  // Models sometimes split one expectation subscript into two subscripts:
  // E_{(x,y)}_{~S}. Merge them into E_{(x,y) \sim S}.
  text = text.replace(
    /(?:\\mathbb\{E\}|E)_\{([^{}]+)\}_\{\s*(?:~|\\sim)\s*([^{}]+)\}/g,
    '\\mathbb{E}_{$1 \\sim $2}',
  )

  // Another frequent PDF transcription is E_ŷ~_{p(.|x)}. Here the second
  // subscript is the sampled distribution, not a subscript attached to `~`.
  text = text.replace(
    /(?:\\mathbb\{E\}|E)_\\hat\{y\}\s*(?:~|\\sim)\s*_\{([^{}]+)\}/g,
    '\\mathbb{E}_{\\hat{y} \\sim $1}',
  )

  // The same expectation notation also appears without braces after PDF
  // extraction: E_(x,y)~S and E_ŷ~p(.|x).
  text = text
    .replace(
      /(?:\\mathbb\{E\}|E)_\(([^()]+)\)\s*(?:~|\\sim)\s*([A-Za-z][A-Za-z0-9]*)/g,
      '\\mathbb{E}_{($1) \\sim $2}',
    )
    .replace(
      /(?:\\mathbb\{E\}|E)_\\hat\{y\}\s*(?:~|\\sim)\s*(p[sS]\s*\([^)]*\))/g,
      '\\mathbb{E}_{\\hat{y} \\sim $1}',
    )

  // Merge other duplicated expectation subscripts such as
  // E_{x_i,t_i}_{i=1}^F, which KaTeX correctly rejects as ambiguous.
  text = text.replace(
    /(?:\\mathbb\{E\}|E)_\{([^{}]+)\}_\{([^{}]+)\}/g,
    '\\mathbb{E}_{$1,\\; $2}',
  )

  // Keep conventional probability notation readable and valid in KaTeX.
  text = text
    .replace(/\bp[sS](?=\s*\()/g, 'p_S')
    .replace(/\bp[tT](?=\s*\()/g, 'p_T')
    .replace(/(?<!\\)\barg\s+min(?=\s*_)/g, '\\arg\\min')
    .replace(/(?<!\\)\bmin(?=\s*_)/g, '\\min')
    .replace(/(?<!\\)\bsum(?=\s*_)/g, '\\sum')
    .replace(/(?<!\\)\blog(?=\s*[([])/g, '\\log')
    .replace(/\(\s*\.\s*\|\s*/g, '(\\cdot\\mid ')
    .replace(/\s*\|\|\s*/g, ' \\,\\|\\, ')

  // Repair plain snake-case hats before grouping the remaining subscripts.
  text = text
    .replace(/\b([A-Za-z]+)_([A-Za-z]+)_hat\b/g, '\\hat{$1}_{$2}')
    .replace(/\b([A-Za-z]+)_hat_([A-Za-z]+)\b/g, '\\hat{$1}_{$2}')

  // Convert plain-text superscripts/subscripts commonly produced from PDF text.
  text = text
    .replace(/([A-Za-z])\s*\*(?=\s*(?:[,)}\]]|=))/g, '$1^*')
    .replace(
      /(\\hat\{[A-Za-z]\}|[A-Za-z])_\s*<\s*([A-Za-z0-9+-]+)\s*>?(?=\s*[,)}\]]|$)/g,
      '$1_{<$2}',
    )

  return text
    .replace(/[ \t]+/g, ' ')
    .replace(/\\([A-Za-z]+)\s+(?=[_^{},)\]])/g, '\\$1')
    .trim()
}
