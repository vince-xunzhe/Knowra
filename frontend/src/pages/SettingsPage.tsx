import { useCallback, useEffect, useState } from 'react'
import { Save, Eye, EyeOff, RefreshCw, Trash2, Key, FolderOpen, Cpu, Image as ImageIcon, GitBranch, AlertTriangle } from 'lucide-react'
import { getConfig, updateConfig, rebuildEdges, resetGraph, type Config } from '../api/client'

export default function SettingsPage() {
  const [config, setConfig] = useState<Partial<Config>>({})
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [saved, setSaved] = useState(false)
  const [loading, setLoading] = useState(true)

  const handleSave = useCallback(async () => {
    const updates: Partial<Config> = { ...config }
    delete updates.available_models
    if (apiKey) updates.openai_api_key = apiKey
    if (updates.openai_api_key && typeof updates.openai_api_key === 'string' && updates.openai_api_key.includes('...')) {
      delete updates.openai_api_key
    }
    await updateConfig(updates as Partial<Omit<Config, 'available_models'>>)
    setSaved(true)
    setApiKey('')
    setTimeout(() => setSaved(false), 2000)
  }, [apiKey, config])

  useEffect(() => {
    getConfig().then(c => {
      setConfig(c)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
        e.preventDefault()
        handleSave()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [handleSave])

  if (loading) return <div className="p-10 text-sm text-slate-500">加载中…</div>

  const models = config.available_models || []
  const embeddingModels = config.available_embedding_models || []
  const selectedModel = models.find(m => m.id === config.vlm_model)

  return (
    <div className="h-full overflow-y-auto relative">
      {/* Floating save bar */}
      <div className="fixed bottom-6 right-8 z-30 flex items-center gap-3 bg-[#0f1117]/95 backdrop-blur-md border border-slate-800 rounded-2xl pl-4 pr-2 py-2 shadow-2xl shadow-black/40">
        {saved && <span className="text-xs text-emerald-300">已保存 ✓</span>}
        <button
          onClick={handleSave}
          className="inline-flex items-center gap-2 bg-indigo-500 hover:bg-indigo-400 text-white text-sm font-medium px-4 py-2 rounded-xl transition-colors shadow-lg shadow-indigo-500/20"
        >
          <Save size={14} />
          保存设置
          <span className="hidden sm:inline text-[10px] text-indigo-200/70 font-mono ml-1">⌘S</span>
        </button>
      </div>
      <div className="max-w-3xl mx-auto px-6 xl:px-8 py-8 pb-24">
        <header className="mb-7">
          <h1 className="text-2xl font-semibold tracking-tight text-white">设置</h1>
          <p className="text-sm text-slate-500 mt-1.5">配置 API、模型与图谱参数，并执行必要的维护操作。</p>
        </header>

        {/* API + scan */}
        <SettingGroup title="基础" description="API 密钥和扫描目录">
          <Field
            icon={<Key size={14} />}
            label="OpenAI API Key"
            hint={config.openai_api_key && !apiKey ? `当前已配置 · ${config.openai_api_key}` : undefined}
          >
            <div className="relative">
              <input
                type={showKey ? 'text' : 'password'}
                placeholder={config.openai_api_key ? '输入新 Key 替换（留空保留当前）' : '输入 API Key'}
                value={apiKey}
                onChange={e => setApiKey(e.target.value)}
                className="w-full bg-slate-900/60 border border-slate-700/60 rounded-lg text-sm text-slate-200 px-3 py-2 pr-10 focus:outline-none focus:border-indigo-500/60 focus:bg-slate-900 transition-colors placeholder:text-slate-500"
              />
              <button
                onClick={() => setShowKey(!showKey)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-200"
              >
                {showKey ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
            </div>
          </Field>

          <Field icon={<FolderOpen size={14} />} label="扫描目录" hint="程序会递归扫描此目录下的所有 PDF 论文">
            <input
              type="text"
              value={config.scan_directory || ''}
              onChange={e => setConfig(c => ({ ...c, scan_directory: e.target.value }))}
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-lg text-sm text-slate-200 px-3 py-2 focus:outline-none focus:border-indigo-500/60 focus:bg-slate-900 transition-colors placeholder:text-slate-500"
              placeholder="/path/to/your/papers"
            />
          </Field>
        </SettingGroup>

        {/* Model */}
        <SettingGroup title="模型" description="配置论文处理模型与图谱向量模型">
          <Field icon={<Cpu size={14} />} label="处理模型">
            <select
              value={config.vlm_model || 'gpt-4o'}
              onChange={e => setConfig(c => ({ ...c, vlm_model: e.target.value }))}
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-lg text-sm text-slate-200 px-3 py-2 focus:outline-none focus:border-indigo-500/60 focus:bg-slate-900 transition-colors appearance-none"
            >
              {models.length === 0 ? (
                <option value="gpt-4o">gpt-4o</option>
              ) : (
                models.map(m => (
                  <option key={m.id} value={m.id}>
                    {m.label} {m.supports_vision ? '[视觉]' : ''} — {m.desc}
                  </option>
                ))
              )}
            </select>
          </Field>

          <Field
            icon={<Cpu size={14} />}
            label="Embedding 模型"
            hint="用于图谱节点向量化与相似度连接计算"
          >
            <select
              value={config.embedding_model || 'text-embedding-3-small'}
              onChange={e => setConfig(c => ({ ...c, embedding_model: e.target.value }))}
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-lg text-sm text-slate-200 px-3 py-2 focus:outline-none focus:border-indigo-500/60 focus:bg-slate-900 transition-colors appearance-none"
            >
              {embeddingModels.length === 0 ? (
                <option value="text-embedding-3-small">text-embedding-3-small</option>
              ) : (
                embeddingModels.map(m => (
                  <option key={m.id} value={m.id}>
                    {m.label} — {m.desc}
                  </option>
                ))
              )}
            </select>
          </Field>

          <label className="flex items-start gap-3 bg-slate-900/40 border border-slate-800 rounded-lg p-4 cursor-pointer hover:border-slate-700 transition-colors">
            <input
              type="checkbox"
              checked={config.use_first_page_image ?? true}
              onChange={e => setConfig(c => ({ ...c, use_first_page_image: e.target.checked }))}
              className="mt-0.5 accent-indigo-500 w-4 h-4"
            />
            <div className="flex-1 min-w-0">
              <p className="text-sm text-slate-200 font-medium flex items-center gap-2">
                <ImageIcon size={13} className="text-slate-500" />
                随文本发送首页图像
              </p>
              <p className="text-xs text-slate-500 mt-1 leading-relaxed">
                首页含标题、作者、摘要，有助于模型准确提取元信息。仅对支持视觉的模型生效。
              </p>
              {selectedModel && !selectedModel.supports_vision && config.use_first_page_image && (
                <p className="text-xs text-amber-400 mt-2 flex items-center gap-1.5">
                  <AlertTriangle size={11} />
                  当前模型不支持视觉，首页图像会被忽略
                </p>
              )}
            </div>
          </label>
        </SettingGroup>

        {/* Graph params */}
        <SettingGroup title="图谱" description="节点相似度连接阈值">
          <Field icon={<GitBranch size={14} />} label="相似度阈值" hint="低阈值产生更多连接，高阈值更精确">
            <div>
              <div className="flex items-center gap-3">
                <button
                  onClick={() => setConfig(c => ({ ...c, similarity_threshold: Math.max(0.4, Math.round(((c.similarity_threshold ?? 0.6) - 0.05) * 100) / 100) }))}
                  className="w-7 h-7 rounded-lg bg-slate-800/80 hover:bg-slate-700 text-slate-300 text-sm leading-none flex items-center justify-center"
                  title="减小 0.05"
                >−</button>
                <div className="relative flex-1 h-7 flex items-center">
                  <input
                    type="range"
                    min="0.4"
                    max="0.9"
                    step="0.05"
                    value={config.similarity_threshold ?? 0.6}
                    onChange={e => setConfig(c => ({ ...c, similarity_threshold: parseFloat(e.target.value) }))}
                    className="absolute inset-0 w-full accent-indigo-500 z-10"
                  />
                  <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 flex justify-between pointer-events-none px-[2px]">
                    {[0.4, 0.5, 0.6, 0.7, 0.8, 0.9].map(t => (
                      <span key={t} className="w-px h-2 bg-slate-700" />
                    ))}
                  </div>
                </div>
                <button
                  onClick={() => setConfig(c => ({ ...c, similarity_threshold: Math.min(0.9, Math.round(((c.similarity_threshold ?? 0.6) + 0.05) * 100) / 100) }))}
                  className="w-7 h-7 rounded-lg bg-slate-800/80 hover:bg-slate-700 text-slate-300 text-sm leading-none flex items-center justify-center"
                  title="增加 0.05"
                >+</button>
                <span className="text-base font-mono tabular-nums text-indigo-300 w-12 text-right">
                  {(config.similarity_threshold ?? 0.6).toFixed(2)}
                </span>
              </div>
              <div className="flex justify-between text-[11px] text-slate-600 mt-1.5 px-9">
                <span>0.40 更多连接</span>
                <span>0.90 更精确</span>
              </div>
            </div>
          </Field>
        </SettingGroup>

        {/* Divider before destructive zone */}
        <div className="my-10 flex items-center gap-3">
          <div className="flex-1 h-px bg-slate-800/80" />
          <span className="text-[10px] tracking-[0.18em] uppercase text-slate-600 font-semibold">维护操作</span>
          <div className="flex-1 h-px bg-slate-800/80" />
        </div>

        {/* Maintenance */}
        <SettingGroup title="图谱维护" description="调整阈值或重新处理">
          <ActionRow
            label="重建相似度边"
            desc="用当前阈值重新计算节点间的相似边，不调用大模型，免费快速。"
            buttonLabel="重建"
            icon={<RefreshCw size={14} />}
            onClick={async () => {
              const r = await rebuildEdges()
              alert(`已重建 · 共 ${r.total_edges} 条边（阈值 ${r.threshold}）`)
            }}
          />
          <ActionRow
            label="重置图谱"
            desc="清空所有知识节点和边，将论文标记为未处理。会重新调用大模型，消耗 API 额度。"
            buttonLabel="清空并重置"
            icon={<Trash2 size={14} />}
            destructive
            onClick={async () => {
              if (!confirm('确认清空所有知识节点？论文会被标记为未处理，需要重新调用大模型。')) return
              await resetGraph()
              alert('已重置，回到图谱页点击「处理论文」重新提取。')
            }}
          />
        </SettingGroup>
      </div>
    </div>
  )
}

function SettingGroup({
  title, description, children,
}: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <section className="mb-8">
      <div className="mb-4">
        <p className="section-label mb-1">{title}</p>
        {description && <p className="panel-subtitle">{description}</p>}
      </div>
      <div className="surface-card p-5 space-y-4">{children}</div>
    </section>
  )
}

function Field({
  icon, label, hint, children,
}: { icon?: React.ReactNode; label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="flex items-center gap-2 text-sm text-slate-300 mb-2">
        {icon && <span className="text-slate-500">{icon}</span>}
        {label}
      </label>
      {children}
      {hint && <p className="text-xs text-slate-500 mt-1.5 leading-relaxed">{hint}</p>}
    </div>
  )
}

function ActionRow({
  label, desc, buttonLabel, icon, onClick, destructive,
}: {
  label: string; desc: string; buttonLabel: string
  icon: React.ReactNode; onClick: () => void; destructive?: boolean
}) {
  return (
    <div className="flex flex-col gap-4 bg-slate-900/40 border border-slate-800 rounded-xl p-4 sm:flex-row sm:items-start sm:justify-between">
      <div className="flex-1 min-w-0">
        <p className="text-sm text-slate-200 font-medium">{label}</p>
        <p className="text-xs text-slate-500 mt-1 leading-relaxed">{desc}</p>
      </div>
      <button
        onClick={onClick}
        className={`shrink-0 inline-flex items-center justify-center gap-1.5 text-sm px-3 py-2 rounded-xl transition-colors ${
          destructive
            ? 'bg-red-500/10 hover:bg-red-500/20 text-red-300 border border-red-500/30'
            : 'bg-slate-800/80 hover:bg-slate-700 text-slate-200 border border-slate-700/50'
        }`}
      >
        {icon} {buttonLabel}
      </button>
    </div>
  )
}
