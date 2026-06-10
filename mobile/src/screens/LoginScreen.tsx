import React, { useState } from 'react'
import {
  ActivityIndicator, KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text,
  TextInput, TouchableOpacity, View,
} from 'react-native'

import { useAuth } from '../contexts/AuthContext'

export default function LoginScreen() {
  const auth = useAuth()
  const [mode, setMode] = useState<'signin' | 'signup'>('signin')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [info, setInfo] = useState<string | null>(null)

  const handleSubmit = async () => {
    setInfo(null)
    try {
      if (mode === 'signin') {
        await auth.signIn(email.trim(), password)
      } else {
        const session = await auth.signUp(email.trim(), password)
        if (!session) {
          setInfo('注册成功，请到邮箱确认后再登录。')
          setMode('signin')
        }
      }
      setPassword('')
    } catch {
      // surfaced via auth.error
    }
  }

  const disabled = !auth.configured || auth.signingIn || !email || !password

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
        <Text style={styles.title}>Knowra</Text>
        <Text style={styles.subtitle}>登录到你的云端知识库</Text>

        {!auth.configured && (
          <View style={styles.warningBox}>
            <Text style={styles.warningText}>
              还没配置云端连接。请先到 <Text style={{ fontWeight: '700' }}>设置</Text> 填写
              Supabase URL / anon key / 云后端 URL。
            </Text>
          </View>
        )}

        <View style={styles.tabsRow}>
          {(['signin', 'signup'] as const).map(m => (
            <TouchableOpacity
              key={m}
              onPress={() => {
                setMode(m)
                auth.clearError()
                setInfo(null)
              }}
              style={[styles.tab, mode === m && styles.tabActive]}
            >
              <Text style={[styles.tabText, mode === m && styles.tabTextActive]}>
                {m === 'signin' ? '登录' : '注册'}
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        <Text style={styles.label}>邮箱</Text>
        <TextInput
          style={styles.input}
          value={email}
          onChangeText={setEmail}
          placeholder="you@example.com"
          placeholderTextColor="#475569"
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="email-address"
          textContentType="emailAddress"
        />

        <Text style={styles.label}>密码</Text>
        <TextInput
          style={styles.input}
          value={password}
          onChangeText={setPassword}
          placeholder="至少 6 位"
          placeholderTextColor="#475569"
          secureTextEntry
          textContentType="password"
        />

        {auth.error && <Text style={styles.error}>{auth.error}</Text>}
        {info && <Text style={styles.info}>{info}</Text>}

        <TouchableOpacity
          style={[styles.button, disabled && styles.buttonDisabled]}
          onPress={handleSubmit}
          disabled={disabled}
        >
          {auth.signingIn
            ? <ActivityIndicator color="#fff" />
            : <Text style={styles.buttonText}>{mode === 'signin' ? '登录' : '注册'}</Text>}
        </TouchableOpacity>

        <Text style={styles.helper}>
          PDF 永远只在桌面端；OpenAI key 也不上传 — 由你按 Ask 调用时本机送一次。
        </Text>
      </ScrollView>
    </KeyboardAvoidingView>
  )
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: '#0b0d12' },
  scroll: { padding: 24, paddingTop: 80 },
  title: { color: '#fff', fontSize: 34, fontWeight: '700', letterSpacing: -0.5 },
  subtitle: { color: '#94a3b8', fontSize: 14, marginTop: 4, marginBottom: 24 },
  tabsRow: {
    flexDirection: 'row', backgroundColor: '#0f1117', borderRadius: 10,
    padding: 4, marginBottom: 20, borderWidth: 1, borderColor: '#1e293b',
  },
  tab: { flex: 1, paddingVertical: 8, borderRadius: 7, alignItems: 'center' },
  tabActive: { backgroundColor: '#312e81' },
  tabText: { color: '#94a3b8', fontSize: 14 },
  tabTextActive: { color: '#e0e7ff', fontWeight: '600' },
  label: {
    color: '#64748b', fontSize: 11, textTransform: 'uppercase', letterSpacing: 1.2,
    marginTop: 12, marginBottom: 6, fontWeight: '600',
  },
  input: {
    backgroundColor: '#0f1117', borderWidth: 1, borderColor: '#1e293b',
    borderRadius: 10, paddingHorizontal: 14, paddingVertical: 12,
    color: '#e2e8f0', fontSize: 15,
  },
  error: {
    color: '#fda4af', fontSize: 13, marginTop: 14,
    backgroundColor: 'rgba(244,63,94,0.08)', padding: 10, borderRadius: 8,
    borderWidth: 1, borderColor: 'rgba(244,63,94,0.3)',
  },
  info: {
    color: '#86efac', fontSize: 13, marginTop: 14,
    backgroundColor: 'rgba(34,197,94,0.08)', padding: 10, borderRadius: 8,
    borderWidth: 1, borderColor: 'rgba(34,197,94,0.3)',
  },
  warningBox: {
    backgroundColor: 'rgba(251,191,36,0.08)', padding: 12, borderRadius: 10,
    borderWidth: 1, borderColor: 'rgba(251,191,36,0.3)', marginBottom: 20,
  },
  warningText: { color: '#fcd34d', fontSize: 13, lineHeight: 20 },
  button: {
    backgroundColor: '#6366f1', borderRadius: 12, paddingVertical: 14,
    alignItems: 'center', marginTop: 24,
  },
  buttonDisabled: { opacity: 0.5 },
  buttonText: { color: '#fff', fontWeight: '600', fontSize: 16 },
  helper: { color: '#475569', fontSize: 11, marginTop: 18, lineHeight: 16, textAlign: 'center' },
})
