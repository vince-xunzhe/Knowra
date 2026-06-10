import React, { useEffect, useState } from 'react'
import {
  ActivityIndicator, KeyboardAvoidingView, Platform, ScrollView, StyleSheet,
  Text, TextInput, TouchableOpacity, View,
} from 'react-native'

import { cloudAsk, getOpenAIKey, type AskResponse } from '../api/cloud'

export default function AskScreen() {
  const [question, setQuestion] = useState('')
  const [history, setHistory] = useState<{ role: 'user' | 'assistant'; content: string }[]>([])
  const [openaiKey, setOpenaiKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [last, setLast] = useState<AskResponse | null>(null)

  useEffect(() => { getOpenAIKey().then(setOpenaiKey) }, [])

  const handleSend = async () => {
    const q = question.trim()
    if (!q) return
    if (!openaiKey) {
      setError('请先到设置填写 OpenAI API key')
      return
    }
    setError(null)
    setBusy(true)
    const nextHistory: typeof history = [...history, { role: 'user', content: q }]
    setHistory(nextHistory)
    setQuestion('')
    try {
      const resp = await cloudAsk(q, openaiKey, history)
      setLast(resp)
      setHistory([...nextHistory, { role: 'assistant', content: resp.answer }])
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={88}
    >
      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
      >
        {history.length === 0 && (
          <View style={styles.empty}>
            <Text style={styles.emptyTitle}>问点什么？</Text>
            <Text style={styles.emptyHint}>
              云端 agent 会从你的概念页 / 论文页里检索相关上下文，调用 OpenAI 回答。
              {'\n'}
              你的 API key 用完即丢，云端不存储。
            </Text>
          </View>
        )}

        {history.map((turn, i) => (
          <View
            key={i}
            style={[styles.bubble, turn.role === 'user' ? styles.userBubble : styles.assistantBubble]}
          >
            <Text style={[styles.bubbleRole, turn.role === 'user' ? styles.userRole : styles.assistantRole]}>
              {turn.role === 'user' ? '你' : 'Knowra'}
            </Text>
            <Text style={styles.bubbleText} selectable>{turn.content}</Text>
          </View>
        ))}

        {last && last.citations.length > 0 && (
          <View style={styles.citationsBox}>
            <Text style={styles.citationsLabel}>引用</Text>
            {last.citations.map((c, i) => (
              <Text key={i} style={styles.citation} selectable>
                · {c.title || c.ref}
              </Text>
            ))}
          </View>
        )}

        {busy && (
          <View style={styles.spinnerRow}>
            <ActivityIndicator color="#818cf8" />
            <Text style={styles.spinnerText}>检索 + 推理中…</Text>
          </View>
        )}

        {error && <Text style={styles.error}>{error}</Text>}
      </ScrollView>

      <View style={styles.inputBar}>
        <TextInput
          style={styles.input}
          value={question}
          onChangeText={setQuestion}
          placeholder="问点什么…"
          placeholderTextColor="#475569"
          multiline
          editable={!busy}
        />
        <TouchableOpacity
          style={[styles.sendButton, (busy || !question.trim()) && styles.sendDisabled]}
          onPress={() => void handleSend()}
          disabled={busy || !question.trim()}
        >
          <Text style={styles.sendText}>发送</Text>
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  )
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: '#0b0d12' },
  scroll: { flex: 1 },
  content: { padding: 16, paddingBottom: 40 },
  empty: { padding: 24, alignItems: 'center', marginTop: 80 },
  emptyTitle: { color: '#cbd5e1', fontSize: 18, fontWeight: '600', marginBottom: 12 },
  emptyHint: { color: '#64748b', fontSize: 13, textAlign: 'center', lineHeight: 20 },
  bubble: { marginBottom: 12, padding: 12, borderRadius: 12, borderWidth: 1 },
  userBubble: { backgroundColor: 'rgba(99,102,241,0.08)', borderColor: 'rgba(99,102,241,0.3)', marginLeft: 32 },
  assistantBubble: { backgroundColor: '#0f1117', borderColor: '#1e293b', marginRight: 16 },
  bubbleRole: { fontSize: 10, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 6 },
  userRole: { color: '#a5b4fc' },
  assistantRole: { color: '#94a3b8' },
  bubbleText: { color: '#e2e8f0', fontSize: 14, lineHeight: 22 },
  citationsBox: {
    backgroundColor: '#0a0a14', padding: 12, borderRadius: 10,
    borderWidth: 1, borderColor: '#1e293b', marginBottom: 12,
  },
  citationsLabel: {
    color: '#64748b', fontSize: 10, textTransform: 'uppercase', letterSpacing: 1.2,
    fontWeight: '600', marginBottom: 6,
  },
  citation: { color: '#94a3b8', fontSize: 12, marginBottom: 3, fontFamily: 'Menlo' },
  spinnerRow: { flexDirection: 'row', alignItems: 'center', padding: 12 },
  spinnerText: { color: '#94a3b8', marginLeft: 10, fontSize: 12 },
  error: {
    color: '#fda4af', fontSize: 12, padding: 10,
    backgroundColor: 'rgba(244,63,94,0.08)', borderRadius: 8,
    borderWidth: 1, borderColor: 'rgba(244,63,94,0.3)',
  },
  inputBar: {
    flexDirection: 'row', alignItems: 'flex-end', padding: 10,
    borderTopWidth: 1, borderTopColor: '#1e293b', backgroundColor: '#0b0d12',
  },
  input: {
    flex: 1, backgroundColor: '#0f1117', borderWidth: 1, borderColor: '#1e293b',
    borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10,
    color: '#e2e8f0', fontSize: 14, maxHeight: 120, marginRight: 8,
  },
  sendButton: {
    backgroundColor: '#6366f1', paddingHorizontal: 16, paddingVertical: 12, borderRadius: 10,
  },
  sendDisabled: { opacity: 0.4 },
  sendText: { color: '#fff', fontWeight: '600' },
})
