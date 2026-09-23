import React, { useCallback, useEffect, useRef, useState } from 'react'
import { ActivityIndicator, FlatList, RefreshControl, StyleSheet, Text, TextInput, TouchableOpacity, View, type ViewToken } from 'react-native'
import { personalRecommendations, recommendationEvent, refreshPersonalRecommendations, saveRecommendationFocus,
  PersonalRecommendationsUnavailableError, type PersonalFeed, type PersonalRecItem } from '../api/cloud'

const lanes = { long_term: '长期兴趣', recent: '当前课题', explore: '相邻探索' }

export default function PersonalRecommendations({ onBrowseAll }: { onBrowseAll: () => void }) {
  const [data, setData] = useState<PersonalFeed | null>(null)
  const [focus, setFocus] = useState('')
  const [error, setError] = useState('')
  const [unavailable, setUnavailable] = useState(false)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const initialized = useRef(false)
  const batch = useRef<string | null>(null)
  const exposed = useRef(new Set<string>())
  const load = useCallback(async () => {
    let next: PersonalFeed
    try { next = await personalRecommendations() }
    catch (error) { setUnavailable(error instanceof PersonalRecommendationsUnavailableError); throw error }
    setUnavailable(false); setError('')
    setData(next)
    batch.current = next.batch?.id ?? null
    if (!initialized.current) { initialized.current = true; setFocus(next.profile.current_focus) }
  }, [])
  useEffect(() => {
    void load().catch(() => setError('无法读取精选，请检查网络或云端服务版本。'))
    const timer = setInterval(() => void load().catch(() => setError('刷新失败，当前显示上次读取的结果。')), 60000)
    return () => clearInterval(timer)
  }, [load])
  async function action(fn: () => Promise<unknown>) {
    setBusy(true); setError('')
    try { await fn(); await load() } catch { setError('操作未完成，请检查连接后重试。') } finally { setBusy(false) }
  }
  const onVisible = useRef(({ viewableItems }: { viewableItems: ViewToken[] }) => {
    if (!batch.current) return
    for (const entry of viewableItems) {
      const item = entry.item as PersonalRecItem
      const key = `${batch.current}:${item.arxiv_id}`
      if (!exposed.current.has(key)) {
        exposed.current.add(key)
        void recommendationEvent(batch.current, item.arxiv_id, 'exposed').catch(() => exposed.current.delete(key))
      }
    }
  }).current
  if (unavailable) return <View style={s.container}><View style={s.content}>
    <Text style={s.heading}>个性化推荐服务尚未就绪</Text>
    <Text style={s.meta}>当前云端服务尚未提供此功能，需要完成服务升级。你的知识库不受影响，可以先浏览全部论文。</Text>
    <TouchableOpacity style={s.button} onPress={onBrowseAll}><Text style={s.notice}>浏览全部论文</Text></TouchableOpacity>
    <TouchableOpacity style={s.button} disabled={busy} onPress={() => void action(load)}><Text style={s.notice}>重新检查服务</Text></TouchableOpacity>
  </View></View>
  if (!data && !error) return <View style={s.center}><ActivityIndicator color="#a5b4fc" /><Text style={s.meta}>正在读取精选…</Text></View>
  return <FlatList style={s.container} contentContainerStyle={s.content} data={data?.items || []}
    keyExtractor={item => item.arxiv_id} onViewableItemsChanged={onVisible}
    viewabilityConfig={{ itemVisiblePercentThreshold: 50, minimumViewTime: 500 }}
    refreshControl={<RefreshControl refreshing={busy} onRefresh={() => void action(load)} tintColor="#a5b4fc" />}
    ListHeaderComponent={<View>
      <Text style={s.heading}>为你精选</Text><Text style={s.meta}>每周一、三、五 · 最多 10 篇 · 长期兴趣优先</Text>
      {!!error && <Text accessibilityRole="alert" style={s.error}>{error}</Text>}
      {!!notice && <Text style={s.notice}>{notice}</Text>}
      {data && <>
        <View style={s.card}><Text style={s.notice}>{data.worker_status === 'online' ? '执行节点在线' : data.worker_status === 'offline' ? '执行节点离线，恢复后补跑' : '尚未连接执行节点，请在桌面端设置'}</Text>
          {data.workers.map(w => <Text key={w.node_id} style={s.meta}>{w.node_id} · {w.health} · {w.last_seen_at ? new Date(w.last_seen_at).toLocaleString() : '尚未连接'}</Text>)}
          <Text style={s.meta}>最近精选：{data.batch?.completed_at ? new Date(data.batch.completed_at).toLocaleString() : '尚未生成'}</Text>
          {data.job?.status !== 'completed' && <Text style={s.meta}>任务：{data.job?.status || '尚未创建'} {data.job?.error || ''}</Text>}
          {!!data.batch?.error && <Text style={s.notice}>{data.batch.error}</Text>}
        </View>
        <Text style={s.meta}>画像来自 {data.profile.paper_count} 篇已同步论文 · 版本 {data.profile.version}</Text>
        <TextInput accessibilityLabel="当前课题" style={s.input} value={focus} onChangeText={setFocus} maxLength={2000} multiline placeholder="当前课题（留空时自动推断）" placeholderTextColor="#64748b" />
        <View style={s.row}><TouchableOpacity disabled={busy} style={s.button} onPress={() => void action(() => saveRecommendationFocus(focus))}><Text style={s.notice}>保存课题</Text></TouchableOpacity>
          <TouchableOpacity disabled={busy} style={s.button} onPress={() => void action(async () => {
            const result = await refreshPersonalRecommendations()
            setNotice(result.status === 'empty_library' ? '请先从桌面同步知识库。' : '精选任务已提交，等待执行节点处理。')
          })}><Text style={s.notice}>更新精选</Text></TouchableOpacity></View>
      </>}
    </View>}
    ListEmptyComponent={data ? <Text style={s.empty}>还没有符合条件的精选。请先同步知识库、连接执行节点，再更新精选。</Text> : null}
    ListFooterComponent={data ? <Text style={s.meta}>已采纳 {data.metrics.adopted} 篇 · 14 天采纳率 {data.metrics.adoption_rate_14d === null ? '等待观察窗口完成' : `${Math.round(data.metrics.adoption_rate_14d * 100)}%`} · 目标 30%。未采纳不会记为不喜欢。</Text> : <TouchableOpacity onPress={() => void action(load)}><Text style={s.notice}>重试</Text></TouchableOpacity>}
    renderItem={({ item }) => {
      const pending = data?.pending_imports.some(p => p.arxiv_id === item.arxiv_id)
      const open = expanded.has(item.arxiv_id)
      return <View style={s.card}>
        <Text style={s.notice}>{lanes[item.lane]} · {item.historical ? '历史补漏' : '近期论文'} · {item.ai ? 'AI 精选' : '基础排序'}</Text>
        <Text style={s.title}>{item.title}</Text><Text style={s.meta}>{item.authors.slice(0, 3).join(', ')} · {item.published?.slice(0, 10)}</Text>
        <Text style={s.reason}>{item.reason}</Text>
        {item.sources.length > 0 && <Text style={s.meta}>关联：{item.sources.map(p => p.title).join('；')}</Text>}
        <TouchableOpacity onPress={() => {
          setExpanded(previous => { const next = new Set(previous); if (open) next.delete(item.arxiv_id); else next.add(item.arxiv_id); return next })
          if (!open && data?.batch) void recommendationEvent(data.batch.id, item.arxiv_id, 'viewed').catch(() => setError('浏览记录未同步，请稍后重试。'))
        }}><Text style={s.link}>{open ? '收起摘要' : '查看摘要与依据'}</Text></TouchableOpacity>
        {open && <><Text style={s.reason}>{item.abstract || '暂无摘要'}</Text>{!!item.evidence && <Text style={s.meta}>{item.evidence}</Text>}</>}
        <TouchableOpacity disabled={busy || pending} style={s.button} onPress={() => void action(async () => {
          if (data?.batch) await recommendationEvent(data.batch.id, item.arxiv_id, 'requested')
          setNotice('入库请求已保存，请在桌面端完成下载与入库；成功后才计入采纳。')
        })}><Text style={s.notice}>{pending ? '待桌面完成入库' : '加入知识库'}</Text></TouchableOpacity>
      </View>
    }} />
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0b0d12' }, content: { padding: 16, paddingBottom: 32 },
  center: { flex: 1, backgroundColor: '#0b0d12', alignItems: 'center', justifyContent: 'center' },
  heading: { color: '#f1f5f9', fontSize: 21, fontWeight: '700', marginBottom: 6 },
  title: { color: '#f1f5f9', fontSize: 16, lineHeight: 23, fontWeight: '600', marginTop: 8 },
  meta: { color: '#94a3b8', fontSize: 12, lineHeight: 19, marginVertical: 4 },
  card: { backgroundColor: '#10141f', borderWidth: 1, borderColor: '#263045', borderRadius: 12, padding: 15, marginVertical: 8 },
  notice: { color: '#a5b4fc', fontSize: 13, lineHeight: 19 }, error: { color: '#fda4af', marginVertical: 10 },
  reason: { color: '#cbd5e1', lineHeight: 22, fontSize: 14, marginVertical: 8 },
  input: { color: '#e2e8f0', backgroundColor: '#10141f', borderColor: '#334155', borderWidth: 1, borderRadius: 8, padding: 12, marginVertical: 8 },
  row: { flexDirection: 'row', gap: 10, marginBottom: 10 },
  button: { borderWidth: 1, borderColor: '#334155', borderRadius: 8, padding: 10, alignSelf: 'flex-start', marginTop: 8 },
  link: { color: '#a5b4fc', paddingVertical: 10 }, empty: { color: '#94a3b8', textAlign: 'center', marginVertical: 30, lineHeight: 22 },
})
