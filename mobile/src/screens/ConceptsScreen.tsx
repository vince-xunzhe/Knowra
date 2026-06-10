import React, { useMemo, useState } from 'react'
import {
  ActivityIndicator, FlatList, RefreshControl, StyleSheet, Text, TextInput,
  TouchableOpacity, View,
} from 'react-native'
import type { NativeStackScreenProps } from '@react-navigation/native-stack'

import { useSnapshot } from '../contexts/SnapshotContext'
import type { RootStackParamList } from '../navigation/types'
import type { KnowledgeNodeRow } from '../api/cloud'

type Props = NativeStackScreenProps<RootStackParamList, 'ConceptsList'>

export default function ConceptsScreen({ navigation }: Props) {
  const snap = useSnapshot()
  const [q, setQ] = useState('')

  const concepts = useMemo(() => {
    const all = (snap.data?.knowledge_nodes ?? []) as KnowledgeNodeRow[]
    // Mobile is mostly for browsing the finished article surface; hide
    // pending / rejected drafts and paper nodes — they're noise here.
    const promoted = all.filter(n =>
      n.promotion_status === 'promoted' && n.node_type !== 'paper')
    const filtered = q.trim()
      ? promoted.filter(n => (n.title || '').toLowerCase().includes(q.toLowerCase()))
      : promoted
    return [...filtered].sort((a, b) => (a.title || '').localeCompare(b.title || ''))
  }, [snap.data, q])

  const wikiByConceptId = useMemo(() => {
    const map = new Map<string, { rel_path: string; download_url: string; title?: string | null }>()
    for (const w of snap.data?.wiki_files ?? []) {
      if (w.kind === 'concept' && w.concept_id) {
        map.set(w.concept_id, { rel_path: w.rel_path, download_url: w.download_url, title: w.title })
      }
    }
    return map
  }, [snap.data])

  if (snap.loading && !snap.data) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator color="#818cf8" />
        <Text style={styles.loadingText}>加载中…</Text>
      </View>
    )
  }

  return (
    <View style={styles.container}>
      <TextInput
        style={styles.search}
        value={q}
        onChangeText={setQ}
        placeholder="搜索概念名"
        placeholderTextColor="#475569"
      />
      <FlatList
        data={concepts}
        keyExtractor={n => n.id}
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
            {snap.data ? '没有已发布的概念' : '云端无数据'}
          </Text>
        }
        renderItem={({ item }) => {
          const wiki = wikiByConceptId.get(item.id)
          return (
            <TouchableOpacity
              style={styles.row}
              onPress={() => {
                if (wiki) {
                  navigation.navigate('WikiDetail', {
                    title: item.title,
                    rel_path: wiki.rel_path,
                    download_url: wiki.download_url,
                  })
                }
              }}
              disabled={!wiki}
            >
              <Text style={styles.rowTitle} numberOfLines={1}>{item.title}</Text>
              <View style={styles.rowMeta}>
                {item.node_type && (
                  <View style={styles.typeChip}>
                    <Text style={styles.typeChipText}>{item.node_type}</Text>
                  </View>
                )}
                {Array.isArray(item.source_paper_ids) && (
                  <Text style={styles.rowMetaText}>
                    {item.source_paper_ids.length} 篇引文
                  </Text>
                )}
                {!wiki && <Text style={styles.noWiki}>未编译</Text>}
              </View>
            </TouchableOpacity>
          )
        }}
      />
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0b0d12', padding: 16 },
  centered: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: '#0b0d12' },
  loadingText: { color: '#94a3b8', fontSize: 13, marginTop: 12 },
  search: {
    backgroundColor: '#0f1117', borderWidth: 1, borderColor: '#1e293b',
    borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10,
    color: '#e2e8f0', marginBottom: 12, fontSize: 14,
  },
  empty: { color: '#64748b', textAlign: 'center', marginTop: 40, fontSize: 13 },
  row: {
    backgroundColor: '#0f1117', borderWidth: 1, borderColor: '#1e293b',
    borderRadius: 10, padding: 14, marginBottom: 10,
  },
  rowTitle: { color: '#f1f5f9', fontSize: 14.5, fontWeight: '500' },
  rowMeta: { flexDirection: 'row', alignItems: 'center', marginTop: 6, flexWrap: 'wrap' },
  rowMetaText: { color: '#64748b', fontSize: 11, marginRight: 8 },
  typeChip: {
    backgroundColor: 'rgba(34,197,94,0.12)', paddingHorizontal: 7, paddingVertical: 2,
    borderRadius: 6, borderWidth: 1, borderColor: 'rgba(34,197,94,0.3)', marginRight: 6,
  },
  typeChipText: { color: '#86efac', fontSize: 10, fontWeight: '500' },
  noWiki: { color: '#475569', fontSize: 10 },
})
