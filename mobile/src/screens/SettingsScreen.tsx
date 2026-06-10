import React, { useEffect, useState } from 'react'
import {
  Alert, Linking, ScrollView, StyleSheet, Text, TextInput, TouchableOpacity, View,
} from 'react-native'

import { useAuth } from '../contexts/AuthContext'
import { CLOUD_DEFAULTS, getCloudConfigOverride, getOpenAIKey, setOpenAIKey } from '../api/cloud'

export default function SettingsScreen() {
  const auth = useAuth()
  // The advanced fields hold the user's explicit OVERRIDE (blank = use the
  // baked-in default). Loaded from raw storage, not from auth.config — which
  // is the merged effective config and would otherwise show defaults as typed.
  const [supabaseUrl, setSupabaseUrl] = useState('')
  const [supabaseAnonKey, setSupabaseAnonKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [openaiKey, setOpenaiKeyState] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    getCloudConfigOverride().then(o => {
      setSupabaseUrl(o.supabaseUrl)
      setSupabaseAnonKey(o.supabaseAnonKey)
      setBaseUrl(o.baseUrl)
    })
    getOpenAIKey().then(setOpenaiKeyState)
  }, [])

  const handleSave = async () => {
    await auth.updateConfig({ supabaseUrl, supabaseAnonKey, baseUrl })
    await setOpenAIKey(openaiKey)
    setSaved(true)
    setTimeout(() => setSaved(false), 1500)
  }

  const handleSignOut = () => {
    Alert.alert('登出', '确定要登出当前云端账号吗？', [
      { text: '取消', style: 'cancel' },
      { text: '登出', style: 'destructive', onPress: () => void auth.signOut() },
    ])
  }

  return (
    <ScrollView style={styles.scroll} contentContainerStyle={styles.content}>
      <Section title="OpenAI Key" desc="云端提问时本机发送一次、用完即弃，云端不存储。通常你只需填这一项。">
        <Field label="API key" value={openaiKey} onChange={setOpenaiKeyState}
               placeholder="sk-..." secure />
        <TouchableOpacity onPress={() => void Linking.openURL('https://platform.openai.com/api-keys')}>
          <Text style={styles.link}>到 platform.openai.com 创建 key →</Text>
        </TouchableOpacity>
      </Section>

      <TouchableOpacity style={styles.advancedToggle} onPress={() => setShowAdvanced(v => !v)}>
        <Text style={styles.advancedToggleText}>
          {showAdvanced ? '▾' : '▸'}  高级：自定义云端连接（默认已连好，一般无需修改）
        </Text>
      </TouchableOpacity>

      {showAdvanced && (
        <Section title="云端连接" desc="默认已连接到 Knowra 云端。仅当你自建后端时才需在此覆盖；留空即用内置默认值。">
          <Field label="Supabase URL" value={supabaseUrl} onChange={setSupabaseUrl}
                 placeholder={CLOUD_DEFAULTS.supabaseUrl} />
          <Field label="Supabase anon key" value={supabaseAnonKey} onChange={setSupabaseAnonKey}
                 placeholder="已内置默认值" secure />
          <Field label="云后端 URL" value={baseUrl} onChange={setBaseUrl}
                 placeholder={CLOUD_DEFAULTS.baseUrl} />
        </Section>
      )}

      <TouchableOpacity style={styles.saveButton} onPress={handleSave}>
        <Text style={styles.saveButtonText}>{saved ? '已保存 ✓' : '保存设置'}</Text>
      </TouchableOpacity>

      {auth.user && (
        <Section title="账号" desc="">
          <View style={styles.accountCard}>
            <Text style={styles.accountEmail}>{auth.user.email || '(无邮箱)'}</Text>
            <Text style={styles.accountId}>{auth.user.id}</Text>
          </View>
          <TouchableOpacity style={styles.signOutButton} onPress={handleSignOut}>
            <Text style={styles.signOutText}>登出</Text>
          </TouchableOpacity>
        </Section>
      )}
    </ScrollView>
  )
}

function Section({ title, desc, children }: { title: string; desc: string; children: React.ReactNode }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {desc ? <Text style={styles.sectionDesc}>{desc}</Text> : null}
      {children}
    </View>
  )
}

function Field({
  label, value, onChange, placeholder, secure,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  secure?: boolean
}) {
  return (
    <View style={styles.fieldRow}>
      <Text style={styles.fieldLabel}>{label}</Text>
      <TextInput
        style={styles.input}
        value={value}
        onChangeText={onChange}
        placeholder={placeholder}
        placeholderTextColor="#475569"
        autoCapitalize="none"
        autoCorrect={false}
        secureTextEntry={secure}
      />
    </View>
  )
}

const styles = StyleSheet.create({
  scroll: { flex: 1, backgroundColor: '#0b0d12' },
  content: { padding: 16, paddingBottom: 60 },
  section: { marginBottom: 24 },
  sectionTitle: { color: '#fff', fontSize: 16, fontWeight: '600', marginBottom: 4 },
  sectionDesc: { color: '#64748b', fontSize: 12, lineHeight: 17, marginBottom: 12 },
  fieldRow: { marginBottom: 12 },
  fieldLabel: {
    color: '#64748b', fontSize: 11, textTransform: 'uppercase',
    letterSpacing: 1.2, marginBottom: 6, fontWeight: '600',
  },
  input: {
    backgroundColor: '#0f1117', borderWidth: 1, borderColor: '#1e293b',
    borderRadius: 10, paddingHorizontal: 12, paddingVertical: 11,
    color: '#e2e8f0', fontSize: 14, fontFamily: 'Menlo',
  },
  link: { color: '#818cf8', fontSize: 13, marginTop: 6 },
  advancedToggle: { paddingVertical: 10, marginBottom: 4 },
  advancedToggleText: { color: '#64748b', fontSize: 13, fontWeight: '500' },
  saveButton: {
    backgroundColor: '#6366f1', borderRadius: 12, paddingVertical: 14,
    alignItems: 'center', marginVertical: 12,
  },
  saveButtonText: { color: '#fff', fontWeight: '600', fontSize: 15 },
  accountCard: {
    backgroundColor: '#0f1117', borderWidth: 1, borderColor: '#1e293b',
    borderRadius: 10, padding: 14, marginBottom: 12,
  },
  accountEmail: { color: '#e2e8f0', fontSize: 14, fontWeight: '600' },
  accountId: { color: '#64748b', fontSize: 10, marginTop: 4, fontFamily: 'Menlo' },
  signOutButton: {
    backgroundColor: 'rgba(244,63,94,0.1)', borderWidth: 1,
    borderColor: 'rgba(244,63,94,0.3)', borderRadius: 10,
    paddingVertical: 11, alignItems: 'center',
  },
  signOutText: { color: '#fda4af', fontSize: 14, fontWeight: '500' },
})
