import React, { useEffect, useMemo, useState } from 'react'
import {
  ActivityIndicator, ScrollView, StyleSheet, Text, TouchableOpacity, View,
} from 'react-native'
import type { NativeStackScreenProps } from '@react-navigation/native-stack'

import { fetchWikiById } from '../api/cloud'
import { useSnapshot } from '../contexts/SnapshotContext'
import MarkdownMathView from '../components/MarkdownMathView'
import type { RootStackParamList } from '../navigation/types'

type Props = NativeStackScreenProps<RootStackParamList, 'PaperDetail'>

/**
 * Mobile mirror of the desktop 回顾 page. Renders the model's
 * structured reading of a paper (parsed from raw_llm_response) plus a
 * toggle to the compiled wiki .md. Both views go through
 * MarkdownMathView so LaTeX in formulas / prose renders via KaTeX
 * (the structured `principle.key_formulas` are LaTeX, and the wiki
 * body is full of `$$…$$`).
 */

// ── structured-extraction shape (subset of desktop PaperExtraction) ──

interface KeyFormula { name?: string; formula?: string; plain?: string }
interface Principle {
  analogy?: string
  architecture_flow?: string
  key_formulas?: KeyFormula[]
}
interface Innovations { previous_work?: string; this_work?: string; why_better?: string }
interface HistoricalPosition { builds_on?: string; inspired?: string; overall?: string }
interface PytorchSnippet { module_name?: string; code?: string; notes?: string }
interface Technique { name?: string; aliases?: string[]; role?: string; builds_on?: string[] }
type DatasetValue = string | { name?: string; purpose?: string; usage?: string }
type NamedValue = string | { name?: string }
type TextValue = string | { short?: string; detail?: string }

interface PaperExtraction {
  abstract_summary?: string
  problem?: string
  motivation?: string
  problem_area?: string
  tech_stack_position?: string
  core_contribution?: string
  principle?: Principle | string
  innovations?: Innovations
  experimental_gains?: string
  historical_position?: HistoricalPosition
  limitations?: string
  pytorch_snippet?: PytorchSnippet
  techniques?: Technique[]
  datasets?: DatasetValue[]
  baselines?: NamedValue[]
  contributions?: TextValue[]
  key_findings?: TextValue[]
  keywords?: string[]
  paper_category?: string
}

/** Parse raw_llm_response into structured extraction. Mirrors the
 *  desktop's cleanup: strip 【†】 citation markers + ```json fences
 *  before JSON.parse so we don't fall back to raw-source display. */
function parseExtraction(raw: string | null | undefined): PaperExtraction | null {
  if (!raw) return null
  let text = raw.replace(/【[^】]*?†[^】]*?】/g, '').trim()
  if (text.startsWith('```')) {
    const lines = text.split('\n')
    if (lines[0]?.startsWith('```')) lines.shift()
    if (lines[lines.length - 1]?.startsWith('```')) lines.pop()
    text = lines.join('\n').trim()
  }
  try {
    const obj = JSON.parse(text)
    return typeof obj === 'object' && obj ? (obj as PaperExtraction) : null
  } catch {
    return null
  }
}

/** Strip $…$ / $$…$$ / \[…\] delimiters so we can re-wrap cleanly. */
function bareFormula(raw: string): string {
  const t = (raw || '').trim()
  if (t.startsWith('$$') && t.endsWith('$$')) return t.slice(2, -2).trim()
  if (t.startsWith('\\[') && t.endsWith('\\]')) return t.slice(2, -2).trim()
  if (t.startsWith('$') && t.endsWith('$')) return t.slice(1, -1).trim()
  return t
}

/**
 * Build a Markdown document from the structured extraction, in the
 * same reading order as the desktop 回顾 page. Formulas become
 * `$$…$$` blocks so KaTeX renders them; inline `$…$` in prose passes
 * through untouched and also renders.
 */
function buildStructuredMarkdown(d: PaperExtraction, category: string | null): string {
  const out: string[] = []
  const push = (s: string) => out.push(s)

  if (d.core_contribution) {
    push(`> **核心贡献**\n>\n> ${d.core_contribution.replace(/\n/g, '\n> ')}`)
  }
  const chips = [category, d.tech_stack_position, d.problem_area].filter(Boolean) as string[]
  if (chips.length) push(chips.map(c => `\`${c}\``).join(' · '))

  if (d.abstract_summary) push(`## 摘要\n\n${d.abstract_summary}`)
  if (d.problem) push(`## 研究问题\n\n${d.problem}`)
  if (d.motivation) push(`## 研究动机\n\n${d.motivation}`)

  const principle: Principle | null =
    typeof d.principle === 'string' ? { analogy: d.principle } : d.principle || null
  if (principle && (principle.analogy || principle.architecture_flow || (principle.key_formulas?.length ?? 0) > 0)) {
    push('## 原理解析（费曼式）')
    if (principle.analogy) push(`**通俗比喻** — ${principle.analogy}`)
    if (principle.architecture_flow) push(`**数据流动** — ${principle.architecture_flow}`)
    for (const f of principle.key_formulas ?? []) {
      if (f?.name) push(`**${f.name}**`)
      if (f?.formula) push(`$$\n${bareFormula(f.formula)}\n$$`)
      if (f?.plain) push(`*${f.plain}*`)
    }
  }

  if (d.innovations && (d.innovations.previous_work || d.innovations.this_work || d.innovations.why_better)) {
    push('## 关键创新点')
    if (d.innovations.previous_work) push(`**以前怎么做** — ${d.innovations.previous_work}`)
    if (d.innovations.this_work) push(`**这篇怎么做** — ${d.innovations.this_work}`)
    if (d.innovations.why_better) push(`**为什么更好** — ${d.innovations.why_better}`)
  }

  if (d.experimental_gains) push(`## 实验效果比前人好在哪\n\n${d.experimental_gains}`)

  if (d.historical_position && (d.historical_position.overall || d.historical_position.builds_on || d.historical_position.inspired)) {
    push('## 背景地位')
    if (d.historical_position.overall) push(d.historical_position.overall)
    if (d.historical_position.builds_on) push(`**站在谁的肩上** — ${d.historical_position.builds_on}`)
    if (d.historical_position.inspired) push(`**启发了谁** — ${d.historical_position.inspired}`)
  }

  if (d.limitations) push(`## 这里的坑（局限性）\n\n${d.limitations}`)

  if (d.pytorch_snippet && d.pytorch_snippet.code) {
    push(`## PyTorch 最简实现${d.pytorch_snippet.module_name ? ` · ${d.pytorch_snippet.module_name}` : ''}`)
    push('```python\n' + d.pytorch_snippet.code + '\n```')
    if (d.pytorch_snippet.notes) push(`*笔记：${d.pytorch_snippet.notes}*`)
  }

  if (Array.isArray(d.key_findings) && d.key_findings.length) {
    push(`## 关键发现`)
    for (const f of d.key_findings) {
      if (typeof f === 'string') push(`- ${f}`)
      else push(`- **${f.short || ''}**${f.detail ? ` — ${f.detail}` : ''}`)
    }
  }

  if (Array.isArray(d.contributions) && d.contributions.length) {
    push(`## 主要贡献`)
    d.contributions.forEach((c, i) => {
      const text = typeof c === 'string' ? c : (c.short || c.detail || '')
      push(`${i + 1}. ${text}`)
    })
  }

  if (Array.isArray(d.techniques) && d.techniques.length) {
    push(`## 技术方法`)
    for (const t of d.techniques) {
      const alias = Array.isArray(t.aliases) && t.aliases.length ? `（别名 ${t.aliases.join(' · ')}）` : ''
      const builds = Array.isArray(t.builds_on) && t.builds_on.length ? ` 基于 ${t.builds_on.join(', ')}` : ''
      push(`- **${t.name || ''}**${t.role ? ` — ${t.role}` : ''}${alias}${builds}`)
    }
  }

  if (Array.isArray(d.datasets) && d.datasets.length) {
    push(`## 数据集`)
    for (const ds of d.datasets) {
      const name = typeof ds === 'string' ? ds : ds.name
      const purpose = typeof ds === 'object' ? (ds.purpose || ds.usage) : null
      push(`- **${name}**${purpose ? ` — ${purpose}` : ''}`)
    }
  }

  if (Array.isArray(d.baselines) && d.baselines.length) {
    const names = d.baselines.map(b => (typeof b === 'string' ? b : b.name)).filter(Boolean)
    push(`## 对比基线\n\n${names.map(n => `\`${n}\``).join(' · ')}`)
  }

  if (Array.isArray(d.keywords) && d.keywords.length) {
    push(`## 关键词\n\n${d.keywords.map(k => `\`${k}\``).join(' · ')}`)
  }

  return out.join('\n\n')
}

type Tab = 'structured' | 'wiki'

export default function PaperDetailScreen({ route }: Props) {
  const { paperId, wikiFileId } = route.params
  const snap = useSnapshot()
  const [tab, setTab] = useState<Tab>('structured')

  const paper = useMemo(
    () => (snap.data?.papers ?? []).find(p => p.id === paperId),
    [snap.data, paperId],
  )
  const extraction = useMemo(
    () => parseExtraction(paper?.raw_llm_response as string | undefined),
    [paper],
  )
  const category = useMemo(() => (
    (paper?.paper_category_override as string | undefined) ||
    (paper?.paper_category_model as string | undefined) ||
    extraction?.paper_category ||
    null
  ), [paper, extraction])

  const structuredMd = useMemo(
    () => (extraction ? buildStructuredMarkdown(extraction, category) : ''),
    [extraction, category],
  )

  return (
    <View style={styles.root}>
      <View style={styles.tabsRow}>
        <Segment label="结构化解读" active={tab === 'structured'} onPress={() => setTab('structured')} />
        <Segment
          label="Wiki 文档"
          active={tab === 'wiki'}
          disabled={!wikiFileId}
          onPress={() => wikiFileId && setTab('wiki')}
        />
      </View>

      {tab === 'structured' ? (
        <StructuredView paper={paper} extraction={extraction} markdown={structuredMd} />
      ) : (
        <WikiView fileId={wikiFileId ?? ''} />
      )}
    </View>
  )
}

// ── structured view ──────────────────────────────────────────────────

function StructuredView({
  paper, extraction, markdown,
}: {
  paper: { error?: unknown; raw_llm_response?: unknown } | undefined
  extraction: PaperExtraction | null
  markdown: string
}) {
  if (paper?.error) {
    return (
      <ScrollView contentContainerStyle={styles.centeredPad}>
        <Text style={styles.errTitle}>处理失败</Text>
        <Text style={styles.errMsg} selectable>{String(paper.error)}</Text>
      </ScrollView>
    )
  }
  if (!paper?.raw_llm_response) {
    return (
      <View style={styles.centered}>
        <Text style={styles.muted}>这篇论文还没有模型解读。{'\n'}在桌面端处理后再同步。</Text>
      </View>
    )
  }
  if (!extraction) {
    // Couldn't parse JSON — show the raw model output as a code block
    // (still readable, no crash).
    return (
      <ScrollView style={styles.scroll}>
        <MarkdownMathView markdown={'```\n' + String(paper.raw_llm_response) + '\n```'} />
      </ScrollView>
    )
  }
  return (
    <ScrollView style={styles.scroll}>
      <MarkdownMathView markdown={markdown} />
    </ScrollView>
  )
}

// ── wiki view ────────────────────────────────────────────────────────

function WikiView({ fileId }: { fileId: string }) {
  const [body, setBody] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setBody(null); setError(null)
    if (!fileId) { setError('该论文没有编译后的 wiki 页'); return }
    // Fetch a fresh signed URL by file id (robust against expiry).
    fetchWikiById(fileId)
      .then(t => { if (!cancelled) setBody(t) })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)) })
    return () => { cancelled = true }
  }, [fileId])

  if (error) {
    return (
      <View style={styles.centered}>
        <Text style={styles.errTitle}>加载失败</Text>
        <Text style={styles.errMsg}>{error}</Text>
        <Text style={styles.muted}>返回列表下拉刷新后再试。</Text>
      </View>
    )
  }
  if (body === null) {
    return <View style={styles.centered}><ActivityIndicator color="#818cf8" /></View>
  }
  return (
    <ScrollView style={styles.scroll}>
      <MarkdownMathView markdown={body} />
    </ScrollView>
  )
}

// ── segmented control ──────────────────────────────────────────────────

function Segment({ label, active, disabled, onPress }: {
  label: string; active: boolean; disabled?: boolean; onPress: () => void
}) {
  return (
    <TouchableOpacity
      style={[styles.segment, active && styles.segmentActive, disabled && styles.segmentDisabled]}
      onPress={onPress}
      disabled={disabled}
    >
      <Text style={[styles.segmentText, active && styles.segmentTextActive]}>{label}</Text>
    </TouchableOpacity>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#0b0d12' },
  scroll: { flex: 1 },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 24, backgroundColor: '#0b0d12' },
  centeredPad: { padding: 24 },
  muted: { color: '#64748b', fontSize: 13, textAlign: 'center', lineHeight: 20, marginTop: 8 },

  tabsRow: {
    flexDirection: 'row', padding: 8, gap: 8,
    borderBottomWidth: 1, borderBottomColor: '#1e293b', backgroundColor: '#0f1117',
  },
  segment: {
    flex: 1, paddingVertical: 9, borderRadius: 9, alignItems: 'center',
    backgroundColor: '#0b0d12', borderWidth: 1, borderColor: '#1e293b',
  },
  segmentActive: { backgroundColor: '#312e81', borderColor: '#4f46e5' },
  segmentDisabled: { opacity: 0.4 },
  segmentText: { color: '#94a3b8', fontSize: 13, fontWeight: '500' },
  segmentTextActive: { color: '#e0e7ff', fontWeight: '700' },

  errTitle: { color: '#fda4af', fontSize: 16, fontWeight: '700' },
  errMsg: { color: '#fda4af', fontSize: 13, marginTop: 8, lineHeight: 20, textAlign: 'center' },
})
