import React, { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ActivityIndicator, Linking, RefreshControl, SectionList, StyleSheet, Text,
  TouchableOpacity, View,
} from 'react-native'
import { Ionicons } from '@expo/vector-icons'

import { useAuth } from '../contexts/AuthContext'
import {
  cloudRecommendations, cloudRecMarks, cloudAddRecMark, cloudRemoveRecMark,
  type RecItem,
} from '../api/cloud'

// Module-level cache so re-opening the tab paints instantly (cleared on a full
// app reload). Mirrors the snapshot cache idea.
let feedCache: RecItem[] | null = null

interface Section { title: string; data: RecItem[]; count: number }

export default function RecommendScreen() {
  const auth = useAuth()
  const [items, setItems] = useState<RecItem[]>(feedCache ?? [])
  const [marks, setMarks] = useState<Set<string>>(new Set())
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(feedCache === null)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const [data, markIds] = await Promise.all([cloudRecommendations(7), cloudRecMarks().catch(() => [])])
      setItems(data.items)
      feedCache = data.items
      setMarks(new Set(markIds))
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      if (!feedCache) setError(msg)
    }
  }, [])

  useEffect(() => {
    if (!auth.user) {
      setLoading(false)
      return
    }
    load().finally(() => setLoading(false))
  }, [auth.user, load])

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await load()
    setRefreshing(false)
  }, [load])

  const toggleMark = (arxivId: string) =>
    setMarks(prev => {
      const next = new Set(prev)
      if (next.has(arxivId)) {
        next.delete(arxivId)
        cloudRemoveRecMark(arxivId).catch(() => {})
      } else {
        next.add(arxivId)
        cloudAddRecMark(arxivId).catch(() => {})
      }
      return next
    })

  const toggleCollapse = (title: string) =>
    setCollapsed(prev => {
      const n = new Set(prev)
      if (n.has(title)) n.delete(title)
      else n.add(title)
      return n
    })

  const sections = useMemo<Section[]>(() => {
    const byTag = new Map<string, RecItem[]>()
    for (const it of items) {
      if (!byTag.has(it.tag)) byTag.set(it.tag, [])
      byTag.get(it.tag)!.push(it)
    }
    return [...byTag.entries()].map(([title, data]) => ({ title, data, count: data.length }))
  }, [items])

  const displaySections = useMemo<Section[]>(
    () => sections.map(s => (collapsed.has(s.title) ? { ...s, data: [] } : s)),
    [sections, collapsed],
  )

  if (!auth.user) {
    return (
      <View style={styles.centered}>
        <Text style={styles.note}>请先登录云端账号</Text>
      </View>
    )
  }
  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator color="#818cf8" />
        <Text style={styles.loadingText}>拉取推荐…</Text>
      </View>
    )
  }
  if (error && items.length === 0) {
    return (
      <View style={styles.centered}>
        <Text style={styles.errorTitle}>加载失败</Text>
        <Text style={styles.errorMsg}>{error}</Text>
        <TouchableOpacity style={styles.retryButton} onPress={() => void onRefresh()}>
          <Text style={styles.retryText}>重试</Text>
        </TouchableOpacity>
      </View>
    )
  }

  return (
    <View style={styles.container}>
      <SectionList
        sections={displaySections}
        keyExtractor={it => it.id}
        stickySectionHeadersEnabled
        contentContainerStyle={{ padding: 16, paddingBottom: 28 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#818cf8" />}
        ListEmptyComponent={<Text style={styles.empty}>还没有推荐。桌面端点「立即检索」拉一批后,这里就会出现。</Text>}
        renderSectionHeader={({ section }) => (
          <TouchableOpacity
            style={styles.sectionHeader}
            activeOpacity={0.6}
            onPress={() => toggleCollapse(section.title)}
          >
            <Text style={styles.sectionChevron}>{collapsed.has(section.title) ? '▸' : '▾'}</Text>
            <Text style={styles.sectionTitle}>{section.title}</Text>
            <Text style={styles.sectionCount}>{section.count}</Text>
          </TouchableOpacity>
        )}
        renderItem={({ item }) => {
          const marked = marks.has(item.arxiv_id)
          const body = item.summary || item.abstract || ''
          return (
            <View style={[styles.card, marked && styles.cardMarked]}>
              <View style={styles.cardTop}>
                <Text style={styles.cardTitle} numberOfLines={3}>{item.title}</Text>
                <TouchableOpacity onPress={() => toggleMark(item.arxiv_id)} hitSlop={8} style={styles.markBtn}>
                  <Ionicons
                    name={marked ? 'bookmark' : 'bookmark-outline'}
                    size={18}
                    color={marked ? '#a5b4fc' : '#64748b'}
                  />
                </TouchableOpacity>
              </View>
              <Text style={styles.cardMeta} numberOfLines={1}>
                {(item.authors || []).slice(0, 3).join(', ')}{item.authors.length > 3 ? ' 等' : ''}
                {item.published ? ` · 发布 ${item.published.slice(0, 10)}` : ''}
                {item.created_at ? ` · 检索 ${item.created_at.slice(0, 10)}` : ''}
              </Text>
              {!!body && (
                <Text style={styles.cardBody}>
                  {item.summary ? '✦ ' : ''}
                  {body.length > 240 ? `${body.slice(0, 240)}…` : body}
                </Text>
              )}
              <View style={styles.cardBottom}>
                {marked && <Text style={styles.pending}>★ 已收藏 · 待本地下载</Text>}
                {item.pdf_url && (
                  <TouchableOpacity onPress={() => void Linking.openURL(item.pdf_url!)} style={styles.arxivBtn}>
                    <Ionicons name="open-outline" size={12} color="#94a3b8" />
                    <Text style={styles.arxivText}>arXiv</Text>
                  </TouchableOpacity>
                )}
              </View>
            </View>
          )
        }}
      />
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0b0d12' },
  centered: { flex: 1, backgroundColor: '#0b0d12', alignItems: 'center', justifyContent: 'center', padding: 24 },
  note: { color: '#94a3b8', fontSize: 14 },
  loadingText: { color: '#94a3b8', fontSize: 13, marginTop: 12 },
  errorTitle: { color: '#fda4af', fontSize: 16, fontWeight: '600' },
  errorMsg: { color: '#fda4af', fontSize: 12, marginTop: 6, textAlign: 'center', fontFamily: 'Menlo' },
  retryButton: { marginTop: 18, paddingHorizontal: 24, paddingVertical: 10, backgroundColor: '#312e81', borderRadius: 10 },
  retryText: { color: '#e0e7ff', fontWeight: '600' },
  empty: { color: '#64748b', textAlign: 'center', marginTop: 40, fontSize: 13, lineHeight: 20 },

  sectionHeader: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    backgroundColor: '#0b0d12', paddingTop: 12, paddingBottom: 8,
  },
  sectionChevron: { color: '#64748b', fontSize: 12, fontWeight: '700' },
  sectionTitle: { color: '#a5b4fc', fontSize: 14, fontWeight: '700', letterSpacing: 0.3 },
  sectionCount: {
    color: '#64748b', fontSize: 11, fontWeight: '600',
    backgroundColor: '#1e293b', borderRadius: 8, paddingHorizontal: 7, paddingVertical: 1, overflow: 'hidden',
  },

  card: { backgroundColor: '#0f1117', borderWidth: 1, borderColor: '#1e293b', borderRadius: 12, padding: 13, marginBottom: 10 },
  cardMarked: { borderColor: 'rgba(129,140,248,0.55)', backgroundColor: 'rgba(99,102,241,0.08)' },
  cardTop: { flexDirection: 'row', alignItems: 'flex-start', gap: 8 },
  cardTitle: { flex: 1, color: '#f1f5f9', fontSize: 14, lineHeight: 19, fontWeight: '600' },
  markBtn: { paddingTop: 1 },
  cardMeta: { color: '#64748b', fontSize: 11, marginTop: 5 },
  cardBody: { color: '#cbd5e1', fontSize: 12.5, lineHeight: 19, marginTop: 7 },
  cardBottom: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 9 },
  pending: { color: '#a5b4fc', fontSize: 11, fontWeight: '600' },
  arxivBtn: { flexDirection: 'row', alignItems: 'center', gap: 3, marginLeft: 'auto' },
  arxivText: { color: '#94a3b8', fontSize: 11 },
})
