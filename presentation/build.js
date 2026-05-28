// Build the Knowra pitch deck.
//
// Visual language is borrowed from the actual product so the deck feels
// like a natural extension of the app, not a generic corporate pitch:
//
//   - Dark slate background everywhere (slate-950 #0b0d12)
//   - Cards on slightly lighter surface (#0f1117) with slate-800 border
//   - Indigo (#6366f1) primary accent (matches the app's brand gradient)
//   - Emerald + amber as secondary accents for status states
//   - One motif: thin colored top-bar on each card (mirrors the
//     "promotion status" chip vocabulary inside the app)
//
// The deck is screenshot-optional: if PNGs are present under
// docs/screenshots/ they get embedded; otherwise the page slides fall
// back to a structured text card describing each page's role.

const fs = require('fs')
const path = require('path')
const pptxgen = require('pptxgenjs')

// ── palette ────────────────────────────────────────────────────────
const COLOR = {
  bg: '0b0d12',
  surface: '0f1117',
  surfaceAlt: '141821',
  border: '1e293b',
  text: 'e2e8f0',
  textMuted: '94a3b8',
  textSubtle: '64748b',
  indigo: '6366f1',
  indigoSoft: '4338ca',
  emerald: '22c55e',
  amber: 'f59e0b',
  rose: 'f43f5e',
  violet: 'a855f7',
}
const FONT = { header: 'Helvetica Neue', body: 'Helvetica Neue' }

const pres = new pptxgen()
pres.layout = 'LAYOUT_WIDE' // 13.3" × 7.5"
pres.title = 'Knowra — Local-First Research Knowledge System'
pres.author = 'Knowra'

const SLIDE_W = 13.3
const SLIDE_H = 7.5

// ── helpers ────────────────────────────────────────────────────────
function newSlide() {
  const s = pres.addSlide()
  s.background = { color: COLOR.bg }
  return s
}

function addPageHeader(slide, tag, title, subtitle) {
  // Small page tag chip
  slide.addShape(pres.shapes.RECTANGLE, {
    x: 0.7, y: 0.55, w: 1.6, h: 0.28,
    fill: { color: COLOR.indigo, transparency: 80 },
    line: { color: COLOR.indigo, width: 0.5, transparency: 50 },
  })
  slide.addText(tag, {
    x: 0.7, y: 0.55, w: 1.6, h: 0.28,
    fontSize: 10, fontFace: FONT.body,
    color: COLOR.indigo, bold: true, align: 'center', valign: 'middle',
    charSpacing: 2, margin: 0,
  })
  // Title
  slide.addText(title, {
    x: 0.7, y: 0.95, w: SLIDE_W - 1.4, h: 0.7,
    fontSize: 30, fontFace: FONT.header, bold: true,
    color: COLOR.text, margin: 0,
  })
  if (subtitle) {
    slide.addText(subtitle, {
      x: 0.7, y: 1.55, w: SLIDE_W - 1.4, h: 0.4,
      fontSize: 14, fontFace: FONT.body,
      color: COLOR.textMuted, margin: 0,
    })
  }
}

function addCard(slide, opts) {
  const { x, y, w, h, accent, title, body, bodySize = 12, titleColor = COLOR.text } = opts
  // Card surface
  slide.addShape(pres.shapes.RECTANGLE, {
    x, y, w, h,
    fill: { color: COLOR.surface },
    line: { color: COLOR.border, width: 0.75 },
  })
  // Accent top-bar
  if (accent) {
    slide.addShape(pres.shapes.RECTANGLE, {
      x, y, w, h: 0.08,
      fill: { color: accent },
      line: { color: accent, width: 0 },
    })
  }
  // Title
  if (title) {
    slide.addText(title, {
      x: x + 0.25, y: y + 0.18, w: w - 0.5, h: 0.35,
      fontSize: 14, fontFace: FONT.header, bold: true,
      color: titleColor, margin: 0,
    })
  }
  // Body
  if (body) {
    const bodyEntries = Array.isArray(body) ? body : [{ text: String(body) }]
    slide.addText(bodyEntries, {
      x: x + 0.25, y: y + 0.6, w: w - 0.5, h: h - 0.8,
      fontSize: bodySize, fontFace: FONT.body,
      color: COLOR.textMuted, margin: 0,
      paraSpaceAfter: 4,
    })
  }
}

function addFooter(slide, text) {
  slide.addText(text || 'Knowra · Local-First Research Knowledge System', {
    x: 0.7, y: SLIDE_H - 0.4, w: SLIDE_W - 1.4, h: 0.25,
    fontSize: 9, fontFace: FONT.body,
    color: COLOR.textSubtle, align: 'left', margin: 0,
  })
}

function pageNumber(slide, n, total) {
  slide.addText(`${n} / ${total}`, {
    x: SLIDE_W - 1.2, y: SLIDE_H - 0.4, w: 0.5, h: 0.25,
    fontSize: 9, fontFace: FONT.body,
    color: COLOR.textSubtle, align: 'right', margin: 0,
  })
}

// Best-effort screenshot embedding: pass a desired w/h, function
// either drops the image or renders a placeholder.
function addScreenshotOrPlaceholder(slide, relPath, opts) {
  const { x, y, w, h, label } = opts
  const fullPath = path.join(__dirname, '..', relPath)
  if (fs.existsSync(fullPath)) {
    slide.addImage({
      path: fullPath, x, y, w, h,
      sizing: { type: 'contain', w, h },
    })
  } else {
    // Placeholder: dashed border + filename
    slide.addShape(pres.shapes.RECTANGLE, {
      x, y, w, h,
      fill: { color: COLOR.surfaceAlt },
      line: { color: COLOR.border, width: 0.75, dashType: 'dash' },
    })
    slide.addText([
      { text: label || '截图待补', options: { fontSize: 12, color: COLOR.textMuted, bold: true, breakLine: true } },
      { text: relPath, options: { fontSize: 9, color: COLOR.textSubtle } },
    ], {
      x, y, w, h, align: 'center', valign: 'middle',
      fontFace: FONT.body, margin: 0,
    })
  }
}

const TOTAL = 12
let page = 0

// ═════════════════════════════════════════════════════════════════════
// Slide 1 — Cover
// ═════════════════════════════════════════════════════════════════════
;(() => {
  page += 1
  const s = newSlide()
  // Decorative indigo halo on the right
  s.addShape(pres.shapes.OVAL, {
    x: SLIDE_W - 5, y: -2, w: 8, h: 8,
    fill: { color: COLOR.indigo, transparency: 90 },
    line: { color: COLOR.indigo, width: 0, transparency: 100 },
  })
  s.addShape(pres.shapes.OVAL, {
    x: SLIDE_W - 3.5, y: -0.5, w: 5, h: 5,
    fill: { color: COLOR.violet, transparency: 92 },
    line: { color: COLOR.violet, width: 0, transparency: 100 },
  })
  // Brand chip
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.9, y: 1.0, w: 1.3, h: 0.32,
    fill: { color: COLOR.emerald, transparency: 80 },
    line: { color: COLOR.emerald, width: 0.5, transparency: 40 },
  })
  s.addText('KNOWRA', {
    x: 0.9, y: 1.0, w: 1.3, h: 0.32,
    fontSize: 11, fontFace: FONT.body, bold: true,
    color: COLOR.emerald, align: 'center', valign: 'middle',
    charSpacing: 4, margin: 0,
  })
  // Title (big)
  s.addText('Local-first research', {
    x: 0.9, y: 1.65, w: 11, h: 0.9,
    fontSize: 50, fontFace: FONT.header, bold: true,
    color: COLOR.text, margin: 0,
  })
  s.addText('knowledge system', {
    x: 0.9, y: 2.55, w: 11, h: 0.9,
    fontSize: 50, fontFace: FONT.header, bold: true,
    color: COLOR.indigo, margin: 0,
  })
  // Subtitle
  s.addText(
    'Scan PDFs → extract structured knowledge → compile a queryable wiki → ask questions across the whole corpus.',
    {
      x: 0.9, y: 3.6, w: 10, h: 0.7,
      fontSize: 16, fontFace: FONT.body,
      color: COLOR.textMuted, margin: 0,
    },
  )
  // Stat row
  const STAT_Y = 5.0
  const STATS = [
    { num: '5', label: 'task-routed providers', color: COLOR.indigo },
    { num: '8', label: 'pluggable LLM tasks', color: COLOR.emerald },
    { num: '4', label: 'lifecycle stages', color: COLOR.amber },
    { num: '1', label: 'compiled wiki layer', color: COLOR.violet },
  ]
  STATS.forEach((st, i) => {
    const x = 0.9 + i * 2.85
    s.addText(st.num, {
      x, y: STAT_Y, w: 2.5, h: 0.9,
      fontSize: 48, fontFace: FONT.header, bold: true,
      color: st.color, margin: 0,
    })
    s.addText(st.label, {
      x, y: STAT_Y + 0.95, w: 2.5, h: 0.3,
      fontSize: 11, fontFace: FONT.body,
      color: COLOR.textMuted, margin: 0,
    })
  })
  s.addText('Powered by a task-routed model gateway · OpenAI · OpenAI-compatible · local Codex CLI', {
    x: 0.9, y: 6.7, w: 11, h: 0.3,
    fontSize: 11, fontFace: FONT.body,
    color: COLOR.textSubtle, italic: true, margin: 0,
  })
})()

// ═════════════════════════════════════════════════════════════════════
// Slide 2 — Problem
// ═════════════════════════════════════════════════════════════════════
;(() => {
  page += 1
  const s = newSlide()
  addPageHeader(
    s,
    '01 · PROBLEM',
    'A reading workflow that doesn\'t compound',
    'Researchers read a lot — but the knowledge stays scattered, and asking "what do I actually know?" is hard.',
  )
  const pains = [
    {
      title: 'PDFs sit in a folder',
      body: 'Once you finish a paper, the knowledge disappears into a static file. There is no structured handle on it later.',
      accent: COLOR.indigo,
    },
    {
      title: 'Notes drift apart from sources',
      body: 'Highlight-and-quote tools collect snippets, but rarely connect them to a paper-level identity.',
      accent: COLOR.amber,
    },
    {
      title: 'No way to ask across papers',
      body: 'Chat-with-PDF apps treat each paper as an island. Cross-paper synthesis falls back to memory.',
      accent: COLOR.rose,
    },
    {
      title: 'LLM costs aren\'t observable',
      body: 'Every tool burns tokens differently — without per-task accounting you cannot tune cost vs. quality.',
      accent: COLOR.emerald,
    },
  ]
  pains.forEach((p, i) => {
    const col = i % 2, row = Math.floor(i / 2)
    addCard(s, {
      x: 0.7 + col * 6.05,
      y: 2.4 + row * 2.25,
      w: 5.9, h: 2.0,
      accent: p.accent,
      title: p.title,
      body: p.body,
    })
  })
  addFooter(s)
  pageNumber(s, page, TOTAL)
})()

// ═════════════════════════════════════════════════════════════════════
// Slide 3 — Solution / Big Idea
// ═════════════════════════════════════════════════════════════════════
;(() => {
  page += 1
  const s = newSlide()
  addPageHeader(
    s,
    '02 · SOLUTION',
    'Compile the corpus once. Then ask, lint, and grow it.',
    'Knowra treats your local PDFs as raw material for a compiled, queryable knowledge layer that thickens over time.',
  )
  const pillars = [
    {
      tag: 'COMPILE',
      title: 'Knowledge → markdown',
      body: 'Structured extraction per paper, plus concept pages compiled by LLM, plus a graph of typed edges between them.',
      accent: COLOR.indigo,
      stat: '.md',
    },
    {
      tag: 'QUERY',
      title: 'Ask across the wiki',
      body: 'An agent reads the compiled wiki (not the raw PDFs) — answers are cited, sessions persist, strong answers file back as concepts.',
      accent: COLOR.emerald,
      stat: 'Ask',
    },
    {
      tag: 'MAINTAIN',
      title: 'Health-check loop',
      body: 'Rule layer + single Agent call surfaces thin pages, mergeable pairs, and missing connecting concepts — Karpathy\'s LLM-KB blueprint.',
      accent: COLOR.amber,
      stat: 'Lint',
    },
  ]
  pillars.forEach((p, i) => {
    const x = 0.7 + i * 4.05
    const y = 2.5
    const w = 3.85, h = 3.9
    addCard(s, { x, y, w, h, accent: p.accent })
    s.addText(p.tag, {
      x: x + 0.25, y: y + 0.25, w: w - 0.5, h: 0.25,
      fontSize: 10, fontFace: FONT.body, bold: true,
      color: p.accent, charSpacing: 3, margin: 0,
    })
    s.addText(p.stat, {
      x: x + 0.25, y: y + 0.6, w: w - 0.5, h: 1.3,
      fontSize: 48, fontFace: FONT.header, bold: true,
      color: COLOR.text, margin: 0,
    })
    s.addText(p.title, {
      x: x + 0.25, y: y + 2.0, w: w - 0.5, h: 0.5,
      fontSize: 16, fontFace: FONT.header, bold: true,
      color: COLOR.text, margin: 0,
    })
    s.addText(p.body, {
      x: x + 0.25, y: y + 2.55, w: w - 0.5, h: 1.2,
      fontSize: 12, fontFace: FONT.body,
      color: COLOR.textMuted, margin: 0,
    })
  })
  s.addText('Built around a task-routed model gateway: every LLM step has its own logical task with its own provider binding and telemetry row.', {
    x: 0.7, y: 6.7, w: SLIDE_W - 1.4, h: 0.3,
    fontSize: 11, fontFace: FONT.body, italic: true,
    color: COLOR.textSubtle, align: 'center', margin: 0,
  })
  addFooter(s)
  pageNumber(s, page, TOTAL)
})()

// ═════════════════════════════════════════════════════════════════════
// Slide 4 — End-to-End Flow
// ═════════════════════════════════════════════════════════════════════
;(() => {
  page += 1
  const s = newSlide()
  addPageHeader(
    s,
    '03 · FLOW',
    'End-to-end pipeline',
    'Raw PDFs become a graph + wiki + ask-able knowledge layer through 6 LLM-routed stages.',
  )
  const STAGES = [
    { tag: 'Scan', text: 'Local PDFs', sub: 'data/papers/', color: COLOR.textSubtle },
    { tag: 'Extract', text: 'paper_extract', sub: 'JSON', color: COLOR.indigo },
    { tag: 'Build', text: 'Graph + Edges', sub: 'similar+', color: COLOR.violet },
    { tag: 'Compile', text: 'wiki_compile', sub: '.md', color: COLOR.amber },
    { tag: 'Ask', text: 'ask_agent', sub: 'cross-wiki', color: COLOR.emerald },
    { tag: 'Maintain', text: 'wiki_lint', sub: 'health', color: COLOR.rose },
  ]
  const BOX_W = 1.85
  const BOX_H = 1.45
  const GAP = 0.18
  const TOTAL_W = STAGES.length * BOX_W + (STAGES.length - 1) * GAP
  const startX = (SLIDE_W - TOTAL_W) / 2
  const boxY = 3.4

  STAGES.forEach((st, i) => {
    const x = startX + i * (BOX_W + GAP)
    addCard(s, {
      x, y: boxY, w: BOX_W, h: BOX_H,
      accent: st.color,
    })
    s.addText(st.tag, {
      x: x + 0.15, y: boxY + 0.2, w: BOX_W - 0.3, h: 0.3,
      fontSize: 10, fontFace: FONT.body, bold: true,
      color: st.color, charSpacing: 3, margin: 0,
    })
    s.addText(st.text, {
      x: x + 0.15, y: boxY + 0.55, w: BOX_W - 0.3, h: 0.45,
      fontSize: 14, fontFace: FONT.header, bold: true,
      color: COLOR.text, margin: 0,
    })
    s.addText(st.sub, {
      x: x + 0.15, y: boxY + 1.02, w: BOX_W - 0.3, h: 0.3,
      fontSize: 10, fontFace: FONT.body,
      color: COLOR.textMuted, margin: 0,
    })
    // Arrow to next
    if (i < STAGES.length - 1) {
      const arrowX = x + BOX_W + 0.02
      s.addShape(pres.shapes.RIGHT_TRIANGLE, {
        x: arrowX, y: boxY + BOX_H / 2 - 0.08, w: 0.13, h: 0.16,
        fill: { color: COLOR.textSubtle },
        line: { color: COLOR.textSubtle, width: 0 },
        rotate: 90,
      })
    }
  })

  // Footnote: telemetry row
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.7, y: 5.55, w: SLIDE_W - 1.4, h: 0.7,
    fill: { color: COLOR.surface },
    line: { color: COLOR.border, width: 0.5 },
  })
  s.addText([
    { text: 'TELEMETRY · ', options: { bold: true, color: COLOR.emerald, charSpacing: 2 } },
    { text: 'Every LLM call writes a row to ', options: { color: COLOR.textMuted } },
    { text: 'llm_calls', options: { color: COLOR.text, fontFace: 'Consolas' } },
    { text: ' (task / provider / model / surface / tokens / latency / success). The dashboard\'s cost panel reads this directly.', options: { color: COLOR.textMuted } },
  ], {
    x: 0.95, y: 5.55, w: SLIDE_W - 1.9, h: 0.7,
    fontSize: 12, fontFace: FONT.body, valign: 'middle', margin: 0,
  })
  addFooter(s)
  pageNumber(s, page, TOTAL)
})()

// ═════════════════════════════════════════════════════════════════════
// Slide 5 — Page · Knowledge Graph
// ═════════════════════════════════════════════════════════════════════
;(() => {
  page += 1
  const s = newSlide()
  addPageHeader(
    s,
    '04 · PAGE',
    '知识 · Knowledge Graph',
    'Main workspace. Pipeline Console on the left, Cytoscape canvas in the middle, node detail drawer on the right.',
  )
  // Big screenshot area
  addScreenshotOrPlaceholder(s, 'docs/screenshots/01-graph-default.png', {
    x: 0.7, y: 2.3, w: 8.0, h: 4.5,
    label: '【知识】默认视图',
  })
  // Sub-feature pills on the right
  const SUB = [
    { tag: '①', title: '录入 / 处理', body: 'Scan PDFs, run extraction, surface failure modes.', color: COLOR.indigo },
    { tag: '②', title: '筛选 / 概念精选', body: 'Heuristic + Agent promotion, manual review, rescue rejected.', color: COLOR.amber },
    { tag: '③', title: '编译 / Wiki', body: 'Recompile paper pages & concept pages on demand.', color: COLOR.violet },
    { tag: '④', title: '健检 / Lint', body: 'Find thin pages, mergeable pairs, missing concepts.', color: COLOR.rose },
  ]
  SUB.forEach((p, i) => {
    addCard(s, {
      x: 9.0, y: 2.3 + i * 1.18, w: 3.6, h: 1.05,
      accent: p.color,
    })
    s.addText(p.tag, {
      x: 9.15, y: 2.5 + i * 1.18, w: 0.5, h: 0.6,
      fontSize: 26, fontFace: FONT.header, bold: true,
      color: p.color, margin: 0,
    })
    s.addText(p.title, {
      x: 9.65, y: 2.45 + i * 1.18, w: 2.9, h: 0.3,
      fontSize: 13, fontFace: FONT.header, bold: true,
      color: COLOR.text, margin: 0,
    })
    s.addText(p.body, {
      x: 9.65, y: 2.75 + i * 1.18, w: 2.9, h: 0.6,
      fontSize: 10, fontFace: FONT.body,
      color: COLOR.textMuted, margin: 0,
    })
  })
  addFooter(s)
  pageNumber(s, page, TOTAL)
})()

// ═════════════════════════════════════════════════════════════════════
// Slide 6 — Page · Papers
// ═════════════════════════════════════════════════════════════════════
;(() => {
  page += 1
  const s = newSlide()
  addPageHeader(
    s,
    '05 · PAGE',
    '论文 · Papers',
    'Paper library. Scan a directory, see per-paper processing state, batch-retry failed ones, drill into raw extraction.',
  )
  addScreenshotOrPlaceholder(s, 'docs/screenshots/05-papers.png', {
    x: 0.7, y: 2.3, w: 8.5, h: 4.5,
    label: '【论文】论文库',
  })
  // Right-side description
  addCard(s, {
    x: 9.5, y: 2.3, w: 3.1, h: 4.5,
    accent: COLOR.indigo,
    title: 'Why it matters',
  })
  s.addText([
    { text: 'Single source of truth', options: { bold: true, color: COLOR.text, breakLine: true, fontSize: 12 } },
    { text: 'All processing flows out of this list — no hidden state, no orphan files.', options: { color: COLOR.textMuted, breakLine: true } },
    { text: '\n', options: { breakLine: true } },
    { text: 'Failure-aware', options: { bold: true, color: COLOR.text, breakLine: true, fontSize: 12 } },
    { text: 'Failed extractions stay marked, with last-error-stage / reason / recoverable-flag persisted.', options: { color: COLOR.textMuted, breakLine: true } },
    { text: '\n', options: { breakLine: true } },
    { text: 'Idempotent', options: { bold: true, color: COLOR.text, breakLine: true, fontSize: 12 } },
    { text: 'Re-scan adds new PDFs but never re-processes ones already in the DB unless you ask.', options: { color: COLOR.textMuted } },
  ], {
    x: 9.7, y: 2.85, w: 2.75, h: 3.85,
    fontSize: 11, fontFace: FONT.body,
    color: COLOR.textMuted, margin: 0,
    paraSpaceAfter: 6,
  })
  addFooter(s)
  pageNumber(s, page, TOTAL)
})()

// ═════════════════════════════════════════════════════════════════════
// Slide 7 — Page · Review
// ═════════════════════════════════════════════════════════════════════
;(() => {
  page += 1
  const s = newSlide()
  addPageHeader(
    s,
    '06 · PAGE',
    '回顾 · Review',
    'Per-paper workspace: structured extraction, personal notes, follow-up chat, and in-app PDF reader.',
  )
  // Two-image layout: review default + pdf open
  addScreenshotOrPlaceholder(s, 'docs/screenshots/06-review-default.png', {
    x: 0.7, y: 2.3, w: 6.0, h: 4.0,
    label: '【回顾】默认视图',
  })
  addScreenshotOrPlaceholder(s, 'docs/screenshots/07-review-pdf-open.png', {
    x: 6.85, y: 2.3, w: 6.0, h: 4.0,
    label: '【回顾】PDF 浮窗打开',
  })
  // Caption strip
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.7, y: 6.45, w: SLIDE_W - 1.4, h: 0.65,
    fill: { color: COLOR.surface },
    line: { color: COLOR.border, width: 0.5 },
  })
  s.addShape(pres.shapes.RECTANGLE, {
    x: 0.7, y: 6.45, w: 0.08, h: 0.65,
    fill: { color: COLOR.emerald },
    line: { color: COLOR.emerald, width: 0 },
  })
  s.addText([
    { text: 'POSITION-PRESERVING PDF · ', options: { bold: true, color: COLOR.emerald, charSpacing: 2 } },
    { text: 'Side-anchored reader covers notes + first-page but never the structured column. Zoom anchors on the visual center; close-and-reopen resumes at the same paragraph.', options: { color: COLOR.textMuted } },
  ], {
    x: 1.0, y: 6.45, w: SLIDE_W - 1.7, h: 0.65,
    fontSize: 11, fontFace: FONT.body, valign: 'middle', margin: 0,
  })
  addFooter(s)
  pageNumber(s, page, TOTAL)
})()

// ═════════════════════════════════════════════════════════════════════
// Slide 8 — Page · Dashboard
// ═════════════════════════════════════════════════════════════════════
;(() => {
  page += 1
  const s = newSlide()
  addPageHeader(
    s,
    '07 · PAGE',
    '看板 · Dashboard',
    'Read-only board describing the whole KB. One backend aggregation → every widget reflects the same snapshot.',
  )
  addScreenshotOrPlaceholder(s, 'docs/screenshots/08-dashboard.png', {
    x: 0.7, y: 2.3, w: 8.0, h: 4.5,
    label: '【看板】数据看板',
  })
  const WIDGETS = [
    { name: 'Radar', body: 'Top-6 tags × papers / concepts / edge density', color: COLOR.indigo },
    { name: 'Growth', body: '12-week timeline from existing timestamps', color: COLOR.emerald },
    { name: 'Distribution', body: 'Paper category + node type pies with chip legend', color: COLOR.amber },
    { name: 'Curation', body: 'status × promoted_by stacked bar + oldest pending', color: COLOR.violet },
    { name: 'Network', body: 'Hub top-10 by degree, orphans, avg degree', color: COLOR.rose },
    { name: 'LLM Cost', body: '30d calls / tokens / latency by task and model', color: COLOR.emerald },
  ]
  WIDGETS.forEach((w, i) => {
    addCard(s, {
      x: 9.0, y: 2.3 + i * 0.78, w: 3.6, h: 0.7,
      accent: w.color,
    })
    s.addText(w.name, {
      x: 9.2, y: 2.4 + i * 0.78, w: 1.1, h: 0.4,
      fontSize: 12, fontFace: FONT.header, bold: true,
      color: w.color, margin: 0, valign: 'middle',
    })
    s.addText(w.body, {
      x: 10.3, y: 2.4 + i * 0.78, w: 2.25, h: 0.5,
      fontSize: 10, fontFace: FONT.body,
      color: COLOR.textMuted, margin: 0, valign: 'middle',
    })
  })
  addFooter(s)
  pageNumber(s, page, TOTAL)
})()

// ═════════════════════════════════════════════════════════════════════
// Slide 9 — Page · Settings
// ═════════════════════════════════════════════════════════════════════
;(() => {
  page += 1
  const s = newSlide()
  addPageHeader(
    s,
    '08 · PAGE',
    '设置 · Settings — task-routed model gateway',
    'Bind a model per logical task. Mix providers freely. Cheap tasks go to local Codex; heavy reasoning routes to your best provider.',
  )
  addScreenshotOrPlaceholder(s, 'docs/screenshots/09-settings.png', {
    x: 0.7, y: 2.3, w: 6.5, h: 4.5,
    label: '【设置】Settings',
  })
  // Right: task → provider table
  addCard(s, {
    x: 7.4, y: 2.3, w: 5.2, h: 4.5,
    accent: COLOR.indigo,
    title: '8 tasks · 3 provider families',
  })
  const TASKS = [
    ['paper_extract', 'OpenAI VLM · Codex'],
    ['paper_chat', 'OpenAI VLM · Codex'],
    ['embedding', 'OpenAI embedding'],
    ['wiki_compile', 'Any text route'],
    ['ask_agent', 'OpenAI · compat · Codex'],
    ['ask_synthesis', 'OpenAI · compat · Codex'],
    ['promotion_judge', 'OpenAI · compat · Codex'],
    ['wiki_lint', 'OpenAI · compat · Codex'],
  ]
  TASKS.forEach((row, i) => {
    const ry = 2.95 + i * 0.45
    if (i % 2 === 0) {
      s.addShape(pres.shapes.RECTANGLE, {
        x: 7.55, y: ry, w: 4.9, h: 0.42,
        fill: { color: COLOR.surfaceAlt },
        line: { color: COLOR.surfaceAlt, width: 0 },
      })
    }
    s.addText(row[0], {
      x: 7.7, y: ry, w: 2.0, h: 0.42,
      fontSize: 11, fontFace: 'Consolas',
      color: COLOR.indigo, valign: 'middle', margin: 0,
    })
    s.addText(row[1], {
      x: 9.7, y: ry, w: 2.7, h: 0.42,
      fontSize: 11, fontFace: FONT.body,
      color: COLOR.textMuted, valign: 'middle', margin: 0,
    })
  })
  addFooter(s)
  pageNumber(s, page, TOTAL)
})()

// ═════════════════════════════════════════════════════════════════════
// Slide 10 — Health-check (Karpathy blueprint)
// ═════════════════════════════════════════════════════════════════════
;(() => {
  page += 1
  const s = newSlide()
  addPageHeader(
    s,
    '09 · DEEP DIVE',
    'Wiki health-check loop',
    'Karpathy\'s LLM knowledge-base blueprint, made concrete: rule layer + one bounded Agent call + applyable report.',
  )
  // Flow boxes
  const flow = [
    { tag: 'INPUT', name: 'Compiled wiki', sub: '.md + index', color: COLOR.indigo },
    { tag: 'RULES', name: 'Zero-token scan', sub: '待充实 · 可合并 · 待建概念', color: COLOR.amber },
    { tag: 'AGENT', name: 'Single LLM call', sub: 'enrich / merge / drop verdicts', color: COLOR.violet },
    { tag: 'OUTPUT', name: 'lint-report.md', sub: 'alias-tagged · Obsidian-readable', color: COLOR.emerald },
  ]
  const FBOX_W = 2.95
  const FBOX_H = 1.5
  const FGAP = 0.18
  const FTOT = flow.length * FBOX_W + (flow.length - 1) * FGAP
  const FSX = (SLIDE_W - FTOT) / 2
  flow.forEach((f, i) => {
    const x = FSX + i * (FBOX_W + FGAP)
    addCard(s, { x, y: 3.0, w: FBOX_W, h: FBOX_H, accent: f.color })
    s.addText(f.tag, {
      x: x + 0.2, y: 3.15, w: FBOX_W - 0.4, h: 0.3,
      fontSize: 10, fontFace: FONT.body, bold: true,
      color: f.color, charSpacing: 3, margin: 0,
    })
    s.addText(f.name, {
      x: x + 0.2, y: 3.5, w: FBOX_W - 0.4, h: 0.5,
      fontSize: 16, fontFace: FONT.header, bold: true,
      color: COLOR.text, margin: 0,
    })
    s.addText(f.sub, {
      x: x + 0.2, y: 4.0, w: FBOX_W - 0.4, h: 0.45,
      fontSize: 11, fontFace: FONT.body,
      color: COLOR.textMuted, margin: 0,
    })
    if (i < flow.length - 1) {
      const ax = x + FBOX_W + 0.02
      s.addShape(pres.shapes.RIGHT_TRIANGLE, {
        x: ax, y: 3.0 + FBOX_H / 2 - 0.08, w: 0.13, h: 0.16,
        fill: { color: COLOR.textSubtle },
        line: { color: COLOR.textSubtle, width: 0 },
        rotate: 90,
      })
    }
  })
  // Bottom: applyable actions row
  const ACTIONS = [
    { name: 'Recompile', body: 'For pages with new source material', color: COLOR.indigo },
    { name: 'Merge / Accept', body: 'For single-paper stubs', color: COLOR.amber },
    { name: 'Ask follow-up', body: 'Feeds back into the wiki loop', color: COLOR.emerald },
  ]
  ACTIONS.forEach((a, i) => {
    addCard(s, {
      x: 0.7 + i * 4.2, y: 5.4, w: 4.0, h: 1.5,
      accent: a.color,
      title: a.name,
      body: a.body,
      titleColor: a.color,
    })
  })
  addFooter(s)
  pageNumber(s, page, TOTAL)
})()

// ═════════════════════════════════════════════════════════════════════
// Slide 11 — Differentiators
// ═════════════════════════════════════════════════════════════════════
;(() => {
  page += 1
  const s = newSlide()
  addPageHeader(
    s,
    '10 · WHY IT\'S DIFFERENT',
    'Four design choices most "chat-with-PDF" tools skip',
    'Each one compounds: token cost, recall quality, and UX all improve as the corpus grows.',
  )
  const items = [
    {
      no: '01',
      title: 'Task-routed model gateway',
      body: 'Per-task provider binding. Cheap operations (paper_chat, embedding) on one model; heavy reasoning (ask, lint) on another. Every call recorded.',
      color: COLOR.indigo,
    },
    {
      no: '02',
      title: 'Compile, don\'t retrieve raw',
      body: 'Ask reads the compiled wiki .md layer, not the raw PDFs. Answers are grounded in your curated knowledge, not in noisy chunks.',
      color: COLOR.emerald,
    },
    {
      no: '03',
      title: 'Local-first by design',
      body: 'All data on disk under data/. SQLite + plain markdown. Open data/wiki/ as an Obsidian vault and backlinks work out of the box.',
      color: COLOR.amber,
    },
    {
      no: '04',
      title: 'Outputs add up',
      body: 'Ask answers can be filed back as concept pages OR exported as Marp decks / reports — re-indexed and immediately findable by the next Ask.',
      color: COLOR.violet,
    },
  ]
  items.forEach((it, i) => {
    const col = i % 2, row = Math.floor(i / 2)
    const x = 0.7 + col * 6.05, y = 2.4 + row * 2.25
    const w = 5.9, h = 2.0
    addCard(s, { x, y, w, h, accent: it.color })
    s.addText(it.no, {
      x: x + 0.25, y: y + 0.25, w: 1.0, h: 0.5,
      fontSize: 22, fontFace: FONT.header, bold: true,
      color: it.color, margin: 0,
    })
    s.addText(it.title, {
      x: x + 1.4, y: y + 0.3, w: w - 1.6, h: 0.4,
      fontSize: 16, fontFace: FONT.header, bold: true,
      color: COLOR.text, margin: 0,
    })
    s.addText(it.body, {
      x: x + 1.4, y: y + 0.75, w: w - 1.6, h: 1.15,
      fontSize: 12, fontFace: FONT.body,
      color: COLOR.textMuted, margin: 0,
    })
  })
  addFooter(s)
  pageNumber(s, page, TOTAL)
})()

// ═════════════════════════════════════════════════════════════════════
// Slide 12 — Closing
// ═════════════════════════════════════════════════════════════════════
;(() => {
  page += 1
  const s = newSlide()
  s.addShape(pres.shapes.OVAL, {
    x: -2, y: SLIDE_H - 4, w: 7, h: 7,
    fill: { color: COLOR.emerald, transparency: 92 },
    line: { color: COLOR.emerald, width: 0, transparency: 100 },
  })
  s.addShape(pres.shapes.OVAL, {
    x: SLIDE_W - 5, y: -2, w: 7, h: 7,
    fill: { color: COLOR.indigo, transparency: 92 },
    line: { color: COLOR.indigo, width: 0, transparency: 100 },
  })
  s.addText('Read once.', {
    x: 1.0, y: 1.8, w: 11, h: 0.9,
    fontSize: 60, fontFace: FONT.header, bold: true,
    color: COLOR.text, margin: 0,
  })
  s.addText('Compile forever.', {
    x: 1.0, y: 2.75, w: 11, h: 0.9,
    fontSize: 60, fontFace: FONT.header, bold: true,
    color: COLOR.indigo, margin: 0,
  })
  s.addText(
    'Knowra turns the papers you\'ve already read into a knowledge base that grows with every new question you ask.',
    {
      x: 1.0, y: 4.0, w: 11, h: 0.9,
      fontSize: 18, fontFace: FONT.body,
      color: COLOR.textMuted, margin: 0,
    },
  )
  // Tech stack chip strip
  const chips = ['FastAPI', 'SQLite', 'React + Vite', 'TypeScript', 'Cytoscape.js', 'Recharts', 'react-pdf', 'OpenAI SDK', 'Codex CLI']
  let cx = 1.0
  const cy = 5.6
  chips.forEach(c => {
    const w = 0.18 + c.length * 0.10
    s.addShape(pres.shapes.RECTANGLE, {
      x: cx, y: cy, w, h: 0.34,
      fill: { color: COLOR.surface },
      line: { color: COLOR.border, width: 0.5 },
    })
    s.addText(c, {
      x: cx, y: cy, w, h: 0.34,
      fontSize: 11, fontFace: 'Consolas',
      color: COLOR.text, align: 'center', valign: 'middle', margin: 0,
    })
    cx += w + 0.12
  })
  // GitHub-style hint
  s.addText('github.com/vince-xunzhe/Knowra', {
    x: 1.0, y: 6.6, w: 11, h: 0.3,
    fontSize: 12, fontFace: 'Consolas',
    color: COLOR.textSubtle, margin: 0,
  })
})()

// ── write ──────────────────────────────────────────────────────────
pres.writeFile({ fileName: path.join(__dirname, '..', 'docs', 'Knowra-Pitch.pptx') })
  .then(f => console.log('Wrote', f))
  .catch(e => { console.error(e); process.exit(1) })
