/**
 * Home dashboard — the app's landing tab.
 *
 * Everything here is derived from the in-memory snapshot (SnapshotContext),
 * so there are no extra network calls and no new endpoints. Charts are plain
 * <View> bars (no SVG / chart lib) to keep the standalone build dependency
 * surface minimal.
 *
 * Shows: an overview header + last-sync, four headline counts, papers-by-category,
 * a reading timeline (papers by publication year), concept composition
 * (by node_type), and the most recently added papers.
 */
import React, { useMemo } from 'react'
import {
  RefreshControl, ScrollView, StyleSheet, Text, TouchableOpacity, View,
} from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { Ionicons } from '@expo/vector-icons'

import { useAuth } from '../contexts/AuthContext'
import { useSnapshot } from '../contexts/SnapshotContext'
import { categoryOf, categoryRank, paperYear } from '../lib/paperMeta'
import type { KnowledgeNodeRow, PaperRow } from '../api/cloud'

type IoniconName = React.ComponentProps<typeof Ionicons>['name']

// Restrained indigo ramp (light → deep), one hue family — no rainbow. Bars are
// labelled and valued, so colour carries no extra meaning; the gradation just
// adds a little depth without the playful multicolour.
const PALETTE = ['#c7d2fe', '#a5b4fc', '#818cf8', '#6366f1', '#5b54d6', '#4f46e5']

function fmtSync(ts: number | null): string {
  if (!ts) return '从未'
  return new Date(ts).toLocaleString()
}

export default function HomeScreen() {
  // Cross-tab navigation (into the 资料 stack) — loosely typed since the tab
  // navigator has no param list.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const nav = useNavigation<any>()
  const auth = useAuth()
  const snap = useSnapshot()

  const papers = (snap.data?.papers ?? []) as PaperRow[]
  const nodes = (snap.data?.knowledge_nodes ?? []) as KnowledgeNodeRow[]
  const edges = snap.data?.knowledge_edges ?? []
  const wikis = snap.data?.wiki_files ?? []

  // Concepts surfaced on mobile == promoted, non-paper nodes (matches Concepts).
  const concepts = useMemo(
    () => nodes.filter(n => n.promotion_status === 'promoted' && n.node_type !== 'paper'),
    [nodes],
  )

  const wikiByPaperId = useMemo(() => {
    const m = new Map<string, string>()
    for (const w of wikis) if (w.kind === 'paper' && w.paper_id) m.set(w.paper_id, w.id)
    return m
  }, [wikis])

  const categoryData = useMemo(() => {
    const counts = new Map<string, number>()
    for (const p of papers) counts.set(categoryOf(p), (counts.get(categoryOf(p)) || 0) + 1)
    return [...counts.entries()]
      .map(([name, count]) => ({ name, count }))
      .sort((a, b) => categoryRank(a.name) - categoryRank(b.name))
  }, [papers])

  const yearData = useMemo(() => {
    const counts = new Map<number, number>()
    for (const p of papers) {
      const y = paperYear(p)
      if (y) counts.set(y, (counts.get(y) || 0) + 1)
    }
    return [...counts.entries()]
      .map(([year, count]) => ({ year, count }))
      .sort((a, b) => a.year - b.year)
  }, [papers])

  const typeData = useMemo(() => {
    const counts = new Map<string, number>()
    for (const n of concepts) {
      const t = n.node_type || '其他'
      counts.set(t, (counts.get(t) || 0) + 1)
    }
    return [...counts.entries()]
      .map(([type, count]) => ({ type, count }))
      .sort((a, b) => b.count - a.count)
  }, [concepts])

  const recentPapers = useMemo(
    () => [...papers]
      .filter(p => p.processed_at)
      .sort((a, b) => String(b.processed_at).localeCompare(String(a.processed_at)))
      .slice(0, 4),
    [papers],
  )

  const loadingFirst = snap.loading && !snap.data
  const errorFirst = !!snap.error && !snap.data
  const isEmpty = !!snap.data && papers.length === 0 && concepts.length === 0

  const openPaper = (p: PaperRow) => {
    nav.navigate('资料', {
      screen: 'PaperDetail',
      params: {
        paperId: p.id,
        title: p.title || p.filename || '论文',
        wikiFileId: wikiByPaperId.get(p.id) ?? null,
      },
    })
  }

  return (
    <ScrollView
      style={styles.scroll}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl refreshing={snap.loading} onRefresh={() => void snap.refresh()} tintColor="#818cf8" />
      }
    >
      <View style={styles.header}>
        <Text style={styles.headerTitle}>知识库概览</Text>
        <Text style={styles.headerMeta}>
          {auth.user?.email || '已登录'} · 上次同步 {fmtSync(snap.fetchedAt)}
        </Text>
      </View>

      {loadingFirst ? (
        <Skeleton />
      ) : errorFirst ? (
        <View style={styles.notice}>
          <Text style={styles.noticeTitle}>加载失败</Text>
          <Text style={styles.noticeMsg}>{snap.error}</Text>
          <TouchableOpacity style={styles.noticeBtn} onPress={() => void snap.refresh()}>
            <Text style={styles.noticeBtnText}>重试</Text>
          </TouchableOpacity>
        </View>
      ) : isEmpty ? (
        <View style={styles.notice}>
          <Ionicons name="cloud-offline-outline" size={28} color="#475569" />
          <Text style={styles.noticeTitle}>云端还没有数据</Text>
          <Text style={styles.noticeMsg}>先在桌面端处理论文并同步，这里就会出现你的知识库概览。</Text>
          <TouchableOpacity style={styles.noticeBtn} onPress={() => void snap.refresh()}>
            <Text style={styles.noticeBtnText}>刷新</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <>
          <View style={styles.statGrid}>
            <StatCard icon="document-text" label="论文" value={papers.length} color="#c7d2fe" onPress={() => nav.navigate('资料')} />
            <StatCard icon="bulb" label="概念" value={concepts.length} color="#a5b4fc" />
            <StatCard icon="git-network" label="连接" value={edges.length} color="#818cf8" />
            <StatCard icon="book" label="Wiki 页" value={wikis.length} color="#6366f1" />
          </View>

          {categoryData.length > 0 && (
            <Section title="按大类分布" hint={`${papers.length} 篇论文`}>
              <BarList data={categoryData.map(d => ({ label: d.name, value: d.count }))} />
            </Section>
          )}

          {yearData.length > 0 && (
            <Section title="阅读时间线" hint="按论文发表年份">
              <Timeline data={yearData} />
            </Section>
          )}

          {typeData.length > 0 && (
            <Section title="概念构成" hint={`${concepts.length} 个已发布概念`}>
              <BarList data={typeData.map(d => ({ label: d.type, value: d.count }))} offset={1} />
            </Section>
          )}

          {recentPapers.length > 0 && (
            <Section title="最近添加">
              {recentPapers.map(p => (
                <TouchableOpacity key={p.id} style={styles.recentRow} onPress={() => openPaper(p)}>
                  <View style={styles.recentDot} />
                  <View style={styles.recentBody}>
                    <Text style={styles.recentTitle} numberOfLines={1}>{p.title || p.filename || '(未提取标题)'}</Text>
                    <Text style={styles.recentMeta} numberOfLines={1}>
                      {categoryOf(p)}{paperYear(p) ? ` · ${paperYear(p)}` : ''}
                    </Text>
                  </View>
                  <Ionicons name="chevron-forward" size={16} color="#475569" />
                </TouchableOpacity>
              ))}
            </Section>
          )}

          <View style={{ height: 24 }} />
        </>
      )}
    </ScrollView>
  )
}

// ── pieces ──────────────────────────────────────────────────────────────

function StatCard({ icon, label, value, color, onPress }: {
  icon: IoniconName
  label: string
  value: number
  color: string
  onPress?: () => void
}) {
  const body = (
    <>
      <Ionicons name={icon} size={18} color={color} />
      <Text style={styles.statValue}>{value}</Text>
      <Text style={styles.statLabel}>{label}</Text>
    </>
  )
  if (onPress) {
    return (
      <TouchableOpacity style={[styles.statCard, { borderColor: color + '40' }]} onPress={onPress} activeOpacity={0.7}>
        {body}
      </TouchableOpacity>
    )
  }
  return <View style={[styles.statCard, { borderColor: color + '40' }]}>{body}</View>
}

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <View style={styles.sectionHead}>
        <Text style={styles.sectionTitle}>{title}</Text>
        {hint ? <Text style={styles.sectionHint}>{hint}</Text> : null}
      </View>
      <View style={styles.card}>{children}</View>
    </View>
  )
}

function BarList({ data, offset = 0 }: { data: { label: string; value: number }[]; offset?: number }) {
  const max = Math.max(...data.map(d => d.value), 1)
  return (
    <>
      {data.map((d, i) => (
        <View key={d.label} style={styles.barRow}>
          <Text style={styles.barLabel} numberOfLines={1}>{d.label}</Text>
          <View style={styles.barTrack}>
            <View style={[styles.barFill, {
              width: `${Math.max((d.value / max) * 100, 3)}%`,
              backgroundColor: PALETTE[(i + offset) % PALETTE.length],
            }]} />
          </View>
          <Text style={styles.barValue}>{d.value}</Text>
        </View>
      ))}
    </>
  )
}

function Timeline({ data }: { data: { year: number; count: number }[] }) {
  const max = Math.max(...data.map(d => d.count), 1)
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.timelineRow}>
      {data.map((d, i) => (
        <View key={d.year} style={styles.vbarCol}>
          <Text style={styles.vbarCount}>{d.count}</Text>
          <View style={styles.vbarTrack}>
            <View style={[styles.vbarFill, {
              height: `${Math.max((d.count / max) * 100, 4)}%`,
              backgroundColor: PALETTE[i % PALETTE.length],
            }]} />
          </View>
          <Text style={styles.vbarYear}>{d.year}</Text>
        </View>
      ))}
    </ScrollView>
  )
}

function Skeleton() {
  return (
    <View>
      <View style={styles.statGrid}>
        {[0, 1, 2, 3].map(i => <View key={i} style={[styles.statCard, styles.skel]} />)}
      </View>
      {[0, 1].map(i => (
        <View key={i} style={styles.section}>
          <View style={[styles.skelLine, { width: '40%' }]} />
          <View style={[styles.card, { height: 120 }]} />
        </View>
      ))}
    </View>
  )
}

const styles = StyleSheet.create({
  scroll: { flex: 1, backgroundColor: '#0b0d12' },
  content: { padding: 16, paddingTop: 14 },

  header: { marginBottom: 18 },
  headerTitle: { color: '#f1f5f9', fontSize: 20, fontWeight: '700', letterSpacing: 0.2 },
  headerMeta: { color: '#64748b', fontSize: 12, marginTop: 4 },

  // headline stats — 2×2
  statGrid: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between' },
  statCard: {
    width: '48%', backgroundColor: '#0f1117', borderWidth: 1, borderColor: '#1e293b',
    borderRadius: 14, padding: 14, marginBottom: 12,
  },
  statValue: { color: '#f8fafc', fontSize: 26, fontWeight: '800', marginTop: 8, fontVariant: ['tabular-nums'] },
  statLabel: { color: '#94a3b8', fontSize: 12, marginTop: 2 },

  section: { marginTop: 10, marginBottom: 12 },
  sectionHead: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 8 },
  sectionTitle: { color: '#e2e8f0', fontSize: 15, fontWeight: '700' },
  sectionHint: { color: '#64748b', fontSize: 11 },
  card: {
    backgroundColor: '#0f1117', borderWidth: 1, borderColor: '#1e293b',
    borderRadius: 14, padding: 14,
  },

  // horizontal bar
  barRow: { flexDirection: 'row', alignItems: 'center', marginBottom: 10 },
  barLabel: { color: '#cbd5e1', fontSize: 12.5, width: 92 },
  barTrack: { flex: 1, height: 9, backgroundColor: '#1e293b', borderRadius: 5, overflow: 'hidden', marginHorizontal: 8 },
  barFill: { height: '100%', borderRadius: 5 },
  barValue: { color: '#94a3b8', fontSize: 12, fontWeight: '600', width: 28, textAlign: 'right', fontVariant: ['tabular-nums'] },

  // vertical bars (timeline)
  timelineRow: { alignItems: 'flex-end', paddingTop: 4, gap: 14 },
  vbarCol: { alignItems: 'center', width: 30 },
  vbarCount: { color: '#94a3b8', fontSize: 10, marginBottom: 4, fontVariant: ['tabular-nums'] },
  vbarTrack: { height: 100, width: 20, justifyContent: 'flex-end', backgroundColor: '#11151c', borderRadius: 6, overflow: 'hidden' },
  vbarFill: { width: '100%', borderTopLeftRadius: 6, borderTopRightRadius: 6 },
  vbarYear: { color: '#64748b', fontSize: 10, marginTop: 5 },

  // recent papers
  recentRow: { flexDirection: 'row', alignItems: 'center', paddingVertical: 9 },
  recentDot: { width: 6, height: 6, borderRadius: 3, backgroundColor: '#6366f1', marginRight: 10 },
  recentBody: { flex: 1, minWidth: 0 },
  recentTitle: { color: '#e2e8f0', fontSize: 13.5, fontWeight: '500' },
  recentMeta: { color: '#64748b', fontSize: 11, marginTop: 2 },

  // states
  notice: { alignItems: 'center', padding: 28, gap: 8 },
  noticeTitle: { color: '#cbd5e1', fontSize: 15, fontWeight: '600', marginTop: 6 },
  noticeMsg: { color: '#64748b', fontSize: 12.5, textAlign: 'center', lineHeight: 19 },
  noticeBtn: { marginTop: 10, paddingHorizontal: 22, paddingVertical: 9, backgroundColor: '#312e81', borderRadius: 10 },
  noticeBtnText: { color: '#e0e7ff', fontWeight: '600', fontSize: 13 },

  // skeleton
  skel: { height: 86, backgroundColor: '#0f1117' },
  skelLine: { height: 12, borderRadius: 6, backgroundColor: '#0f1117', marginBottom: 10 },
})
