import React, { useEffect, useState } from 'react'
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from 'react-native'
import type { NativeStackScreenProps } from '@react-navigation/native-stack'

import { fetchWikiBody } from '../api/cloud'
import type { RootStackParamList } from '../navigation/types'

type Props = NativeStackScreenProps<RootStackParamList, 'WikiDetail'>

/**
 * Plain-text rendering of the .md body. We don't pull in a full
 * markdown renderer (would balloon bundle); for v1 the user sees the
 * raw markdown and that's already 80% of the value. A v2 can swap in
 * react-native-markdown-display if there's demand.
 */
export default function WikiDetailScreen({ route }: Props) {
  const { download_url, rel_path } = route.params
  const [body, setBody] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setBody(null); setError(null)
    fetchWikiBody(download_url)
      .then(text => { if (!cancelled) setBody(text) })
      .catch(err => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
    return () => { cancelled = true }
  }, [download_url])

  if (error) {
    return (
      <View style={styles.centered}>
        <Text style={styles.errorTitle}>加载失败</Text>
        <Text style={styles.errorMsg}>{error}</Text>
        <Text style={styles.errorHint}>签名 URL 可能已过期 — 返回列表后下拉刷新。</Text>
      </View>
    )
  }

  if (body === null) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator color="#818cf8" />
      </View>
    )
  }

  return (
    <ScrollView style={styles.scroll} contentContainerStyle={styles.content}>
      <Text style={styles.relPath}>{rel_path}</Text>
      <Text style={styles.body} selectable>{body}</Text>
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  scroll: { flex: 1, backgroundColor: '#0b0d12' },
  centered: { flex: 1, backgroundColor: '#0b0d12', alignItems: 'center', justifyContent: 'center', padding: 24 },
  content: { padding: 16, paddingBottom: 60 },
  relPath: { color: '#475569', fontSize: 10, fontFamily: 'Menlo', marginBottom: 14 },
  body: { color: '#e2e8f0', fontSize: 14, lineHeight: 22, fontFamily: 'Menlo' },
  errorTitle: { color: '#fda4af', fontSize: 16, fontWeight: '600' },
  errorMsg: { color: '#fda4af', fontSize: 12, marginTop: 8, fontFamily: 'Menlo', textAlign: 'center' },
  errorHint: { color: '#64748b', fontSize: 11, marginTop: 8, textAlign: 'center' },
})
