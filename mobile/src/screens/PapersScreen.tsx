import React, { useMemo, useState } from 'react'
import {
  ActivityIndicator, RefreshControl, SectionList, StyleSheet, Text, TextInput,
  TouchableOpacity, View,
} from 'react-native'
import type { NativeStackScreenProps } from '@react-navigation/native-stack'

import { useSnapshot } from '../contexts/SnapshotContext'
import type { RootStackParamList } from '../navigation/types'
import type { PaperRow } from '../api/cloud'
import { categoryOf, categoryRank, paperYear } from '../lib/paperMeta'

type Props = NativeStackScreenProps<RootStackParamList, 'PapersList'>

interface Section { title: string; data: PaperRow[] }

export default function PapersScreen({ navigation }: Props) {
  const snap = useSnapshot()
  const [q, setQ] = useState('')

  const wikiByPaperId = useMemo(() => {
    const map = new Map<string, string>()
    for (const w of snap.data?.wiki_files ?? []) {
      if (w.kind === 'paper' && w.paper_id) map.set(w.paper_id, w.id)
    }
    return map
  }, [snap.data])

  // Group papers by category (lanes ordered like 编译图谱); within a lane,
  // order by the paper's publication time — chronological, oldest → newest
  // (unknown years sort last).
  const sections = useMemo<Section[]>(() => {
    const all = (snap.data?.papers ?? []) as PaperRow[]
    const filtered = q.trim()
      ? all.filter(p => (p.title || p.filename || '').toLowerCase().includes(q.toLowerCase()))
      : all

    const yearById = new Map<string, number>()
    for (const p of filtered) yearById.set(p.id, paperYear(p))

    const byCat = new Map<string, PaperRow[]>()
    for (const p of filtered) {
      const c = categoryOf(p)
      if (!byCat.has(c)) byCat.set(c, [])
      byCat.get(c)!.push(p)
    }
    const result: Section[] = []
    for (const cat of [...byCat.keys()].sort((a, b) => categoryRank(a) - categoryRank(b))) {
      const rows = byCat.get(cat)!.sort((a, b) => {
        const ya = yearById.get(a.id) || 9999
        const yb = yearById.get(b.id) || 9999
        if (ya !== yb) return ya - yb
        return String(a.processed_at || '').localeCompare(String(b.processed_at || ''))
      })
      result.push({ title: cat, data: rows })
    }
    return result
  }, [snap.data, q])

  if (snap.loading && !snap.data) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator color="#818cf8" />
        <Text style={styles.loadingText}>从云端拉取快照…</Text>
      </View>
    )
  }

  if (snap.error && !snap.data) {
    return (
      <View style={styles.centered}>
        <Text style={styles.errorTitle}>加载失败</Text>
        <Text style={styles.errorMsg}>{snap.error}</Text>
        <TouchableOpacity style={styles.retryButton} onPress={() => void snap.refresh()}>
          <Text style={styles.retryText}>重试</Text>
        </TouchableOpacity>
      </View>
    )
  }

  const totalPapers = snap.data?.papers.length ?? 0

  return (
    <View style={styles.container}>
      <TextInput
        style={styles.search}
        value={q}
        onChangeText={setQ}
        placeholder="搜索标题或文件名"
        placeholderTextColor="#475569"
      />
      <SectionList
        sections={sections}
        keyExtractor={p => p.id}
        stickySectionHeadersEnabled
        contentContainerStyle={{ paddingBottom: 24 }}
        refreshControl={
          <RefreshControl
            refreshing={snap.loading}
            onRefresh={() => void snap.refresh()}
            tintColor="#818cf8"
          />
        }
        ListEmptyComponent={
          <Text style={styles.empty}>
            {totalPapers === 0
              ? '云端没有论文。先在桌面端处理论文然后同步。'
              : '没有匹配的论文'}
          </Text>
        }
        renderSectionHeader={({ section }) => (
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>{section.title}</Text>
            <Text style={styles.sectionCount}>{section.data.length}</Text>
          </View>
        )}
        renderItem={({ item }) => {
          const wikiFileId = wikiByPaperId.get(item.id) ?? null
          const yr = paperYear(item)
          return (
            <TouchableOpacity
              style={styles.row}
              onPress={() => {
                navigation.navigate('PaperDetail', {
                  paperId: item.id,
                  title: item.title || item.filename || '论文',
                  wikiFileId,
                })
              }}
            >
              <Text style={styles.rowTitle} numberOfLines={2}>
                {item.title || item.filename || '(未提取标题)'}
              </Text>
              <View style={styles.rowMeta}>
                {yr ? <Text style={styles.rowYear}>{yr}</Text> : null}
                <Text style={styles.rowMetaText}>
                  {Array.isArray(item.authors) && item.authors.length > 0
                    ? (item.authors as string[]).slice(0, 2).join(', ')
                    : '匿名'}
                </Text>
                {item.num_pages ? (
                  <Text style={styles.rowMetaText}>· {item.num_pages}p</Text>
                ) : null}
                {!wikiFileId && <Text style={styles.noWiki}>· 未编译 wiki</Text>}
              </View>
            </TouchableOpacity>
          )
        }}
      />
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0b0d12', paddingHorizontal: 16, paddingTop: 16 },
  centered: { flex: 1, backgroundColor: '#0b0d12', alignItems: 'center', justifyContent: 'center', padding: 24 },
  loadingText: { color: '#94a3b8', fontSize: 13, marginTop: 12 },
  errorTitle: { color: '#fda4af', fontSize: 16, fontWeight: '600' },
  errorMsg: { color: '#fda4af', fontSize: 12, marginTop: 6, fontFamily: 'Menlo', textAlign: 'center' },
  retryButton: {
    marginTop: 18, paddingHorizontal: 24, paddingVertical: 10,
    backgroundColor: '#312e81', borderRadius: 10,
  },
  retryText: { color: '#e0e7ff', fontWeight: '600' },
  search: {
    backgroundColor: '#0f1117', borderWidth: 1, borderColor: '#1e293b',
    borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10,
    color: '#e2e8f0', marginBottom: 12, fontSize: 14,
  },
  empty: { color: '#64748b', textAlign: 'center', marginTop: 40, fontSize: 13 },

  sectionHeader: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    backgroundColor: '#0b0d12', paddingTop: 12, paddingBottom: 8,
  },
  sectionTitle: {
    color: '#a5b4fc', fontSize: 13, fontWeight: '700',
    letterSpacing: 0.5, textTransform: 'uppercase',
  },
  sectionCount: {
    color: '#64748b', fontSize: 11, fontWeight: '600',
    backgroundColor: '#1e293b', borderRadius: 8, paddingHorizontal: 7, paddingVertical: 1,
    overflow: 'hidden',
  },

  row: {
    backgroundColor: '#0f1117', borderWidth: 1, borderColor: '#1e293b',
    borderRadius: 10, padding: 14, marginBottom: 10,
  },
  rowTitle: { color: '#f1f5f9', fontSize: 14.5, lineHeight: 20, fontWeight: '500' },
  rowMeta: { flexDirection: 'row', alignItems: 'center', marginTop: 6, flexWrap: 'wrap' },
  rowYear: {
    color: '#a5b4fc', fontSize: 10.5, fontWeight: '700', marginRight: 6,
    backgroundColor: '#1e293b', borderRadius: 6, paddingHorizontal: 6, paddingVertical: 1,
    overflow: 'hidden',
  },
  rowMetaText: { color: '#64748b', fontSize: 11, marginRight: 6 },
  noWiki: { color: '#475569', fontSize: 10, marginLeft: 6 },
})
