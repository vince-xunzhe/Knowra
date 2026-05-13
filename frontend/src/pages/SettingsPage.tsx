import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Save, RefreshCw, Trash2, FolderOpen, Image as ImageIcon,
  GitBranch, PlayCircle, Key, Cpu, ChevronDown, ChevronRight,
} from 'lucide-react'
import {
  getConfig,
  updateConfig,
  rebuildEdges,
  resetGraph,
  testModelGatewayProvider,
  type Config,
  type ModelGatewayConfig,
  type ModelGatewayProvider,
  type ModelGatewayTaskBinding,
  type ModelGatewayTaskSpec,
} from '../api/client'

type ProviderRoute = 'open_api' | 'codex'

const TASK_TYPE_LABELS: Record<ModelGatewayTaskSpec['task_type'], string> = {
  embedding: 'Embedding',
  llm: 'LLM',
  vlm: 'VLM',
}

function emptyGateway(): ModelGatewayConfig {
  return {
    providers: [],
    models: [],
    task_bindings: {},
    task_specs: [],
    available_provider_types: [],
  }
}

function formatTestMeta(provider: ModelGatewayProvider) {
  if (!provider.last_tested_at) return '尚未测试'
  const status = provider.last_test_status === 'ok' ? '最近成功' : provider.last_test_status === 'error' ? '最近失败' : '最近测试'
  return `${status} · ${new Date(provider.last_tested_at).toLocaleString()}`
}

function providerRoute(provider: ModelGatewayProvider | null | undefined): ProviderRoute {
  return provider?.provider_type === 'codex_cli' ? 'codex' : 'open_api'
}

function providerRouteLabel(route: ProviderRoute) {
  return route === 'codex' ? 'Codex' : 'OpenAPI'
}

function providerBrandLabel(provider: ModelGatewayProvider | null | undefined) {
  if (!provider) return '未设置'
  return provider.label || provider.id
}

function normalizeTaskBinding(
  raw: string | ModelGatewayTaskBinding | undefined,
  fallbackModelId: string,
): Required<ModelGatewayTaskBinding> {
  if (typeof raw === 'string') {
    return {
      model_id: raw || fallbackModelId,
      reasoning_effort: 'medium',
    }
  }
  return {
    model_id: raw?.model_id || fallbackModelId,
    reasoning_effort: raw?.reasoning_effort || 'medium',
  }
}

function preferredHealthcheckModel(gateway: ModelGatewayConfig, provider: ModelGatewayProvider) {
  const providerModels = (gateway.models || []).filter(model => model.provider_id === provider.id && model.model_kind === 'chat')
  return (
    provider.healthcheck_model
    || providerModels[0]?.upstream_model
    || (provider.provider_type === 'codex_cli' ? 'gpt-5.4-mini' : 'gpt-4o-mini')
  )
}

function mergeModels(
  registryModels: NonNullable<ModelGatewayConfig['models']>,
  builtinOptions: NonNullable<Config['available_model_gateway_models']> | undefined,
) {
  const merged = new Map<string, ModelGatewayConfig['models'][number]>()
  for (const model of registryModels || []) {
    merged.set(model.id, model)
  }
  for (const option of builtinOptions || []) {
    if (merged.has(option.id)) continue
    if (!option.provider_id || !option.upstream_model || !option.model_kind) continue
    const modelKind = option.model_kind === 'embedding' ? 'embedding' : 'chat'
    merged.set(option.id, {
      id: option.id,
      label: option.label,
      provider_id: option.provider_id,
      upstream_model: option.upstream_model,
      model_kind: modelKind,
      supports_vision: Boolean(option.supports_vision),
      supported_tasks: option.supported_tasks || [],
      builtin: Boolean(option.builtin),
    })
  }
  return Array.from(merged.values())
}

function normalizeGatewayForSubmit(gateway: ModelGatewayConfig): ModelGatewayConfig {
  return {
    ...gateway,
    providers: (gateway.providers || []).map(provider => ({
      ...provider,
      healthcheck_model: preferredHealthcheckModel(gateway, provider),
    })),
    task_specs: [],
    available_provider_types: [],
  }
}

export default function SettingsPage() {
  const [config, setConfig] = useState<Partial<Config>>({})
  const [saved, setSaved] = useState(false)
  const [loading, setLoading] = useState(true)
  const [testingProviderId, setTestingProviderId] = useState<string | null>(null)
  const [selectedProviderId, setSelectedProviderId] = useState('')

  const updateGateway = useCallback((updater: (gateway: ModelGatewayConfig) => ModelGatewayConfig) => {
    setConfig(current => {
      const gateway = current.model_gateway || emptyGateway()
      return { ...current, model_gateway: updater(gateway) }
    })
  }, [])

  const handleSave = useCallback(async () => {
    const updates: Partial<Config> = { ...config }
    delete updates.openai_api_key
    delete updates.available_models
    delete updates.available_embedding_models
    delete updates.available_wiki_compile_models
    delete updates.available_model_gateway_models
    if (updates.model_gateway) {
      updates.model_gateway = normalizeGatewayForSubmit(updates.model_gateway)
    }
    const next = await updateConfig(updates as Partial<Config>)
    setConfig(next)
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }, [config])

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

  const gateway = config.model_gateway || emptyGateway()
  const providers = gateway.providers || []
  const models = useMemo(
    () => mergeModels(gateway.models || [], config.available_model_gateway_models),
    [gateway.models, config.available_model_gateway_models],
  )
  const taskSpecs = gateway.task_specs || []
  const taskBindings = gateway.task_bindings || {}
  const modelById = useMemo(() => Object.fromEntries(models.map(model => [model.id, model])), [models])
  const providerById = useMemo(() => Object.fromEntries(providers.map(provider => [provider.id, provider])), [providers])
  const selectedPaperExtractModel = useMemo(() => {
    const paperTask = taskSpecs.find(task => task.id === 'paper_extract')
    if (!paperTask) return null
    const binding = normalizeTaskBinding(taskBindings.paper_extract, paperTask.recommended_model_id || '')
    return binding.model_id ? modelById[binding.model_id] || null : null
  }, [modelById, taskBindings.paper_extract, taskSpecs])
  const selectedProvider = providers.find(provider => provider.id === selectedProviderId) || providers[0] || null

  useEffect(() => {
    if (providers.length === 0) {
      setSelectedProviderId('')
      return
    }
    if (!providers.some(provider => provider.id === selectedProviderId)) {
      setSelectedProviderId(providers[0].id)
    }
  }, [providers, selectedProviderId])

  const setTaskBinding = useCallback((taskId: string, next: Required<ModelGatewayTaskBinding>) => {
    updateGateway(current => ({
      ...current,
      task_bindings: {
        ...current.task_bindings,
        [taskId]: next,
      },
    }))
  }, [updateGateway])

  const handleTestProvider = useCallback(async (providerId: string) => {
    setTestingProviderId(providerId)
    try {
      const result = await testModelGatewayProvider(providerId, normalizeGatewayForSubmit(gateway))
      setConfig(result.config)
    } finally {
      setTestingProviderId(null)
    }
  }, [gateway])

  if (loading) return <div className="p-10 text-sm text-slate-500">加载中…</div>

  return (
    <div className="h-full overflow-y-auto relative">
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

      <div className="max-w-7xl mx-auto px-6 xl:px-8 py-8 pb-24">
        <header className="mb-7">
          <h1 className="text-2xl font-semibold tracking-tight text-white">设置</h1>
          <p className="text-sm text-slate-500 mt-1.5">以任务为核心配置模型，Provider 区只保留必要连接参数和联通测试。</p>
        </header>

        <SettingGroup
          title="基础"
          description="扫描目录与旧版兼容设置。模型相关配置已经迁移到任务模型。"
        >
          <Field icon={<FolderOpen size={14} />} label="扫描目录" hint="程序会递归扫描此目录下的所有 PDF 论文">
            <input
              type="text"
              value={config.scan_directory || ''}
              onChange={e => setConfig(current => ({ ...current, scan_directory: e.target.value }))}
              className="w-full bg-slate-900/60 border border-slate-700/60 rounded-lg text-sm text-slate-200 px-3 py-2 focus:outline-none focus:border-indigo-500/60 focus:bg-slate-900 transition-colors placeholder:text-slate-500"
              placeholder="/path/to/your/papers"
            />
          </Field>
        </SettingGroup>

        <SettingGroup
          title="任务模型"
          description="每个任务单独选模型。系统推荐只是默认值，你可以按任务改成更便宜或更强的路线。"
          defaultExpanded={false}
        >
          <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-4">
            <div className="mb-4">
              <p className="text-sm font-medium text-slate-100">Provider 联通测试</p>
              <p className="text-xs text-slate-500 mt-1">这里只保留必要连接参数。内部的 provider 元数据和模型注册表不再暴露给你；Qwen 默认预填的是中国（北京）OpenAI 兼容地址，你也可以改成别的区域。</p>
            </div>

            {selectedProvider ? (
              <div className="space-y-4">
                <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_auto] gap-4 items-end">
                  <Field label="当前品牌">
                    <select
                      value={selectedProvider.id}
                      onChange={e => setSelectedProviderId(e.target.value)}
                      className="w-full bg-slate-900/60 border border-slate-700/60 rounded-lg text-[13px] text-slate-200 px-3 py-2 focus:outline-none focus:border-indigo-500/60"
                    >
                      {providers.map(provider => (
                        <option key={provider.id} value={provider.id}>
                          {provider.label}
                        </option>
                      ))}
                    </select>
                  </Field>

                  <button
                    onClick={() => handleTestProvider(selectedProvider.id)}
                    disabled={testingProviderId === selectedProvider.id}
                    className="inline-flex items-center justify-center gap-1.5 text-xs px-3 py-2 rounded-lg bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-200 border border-indigo-500/30 disabled:opacity-60"
                  >
                    <PlayCircle size={12} />
                    {testingProviderId === selectedProvider.id ? '测试中…' : '联通测试'}
                  </button>
                </div>

                {(selectedProvider.provider_type === 'openai' || selectedProvider.provider_type === 'openai_compatible') && (
                  <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                    <Field icon={<Key size={14} />} label="API Key">
                      <input
                        type="password"
                        value={selectedProvider.api_key || ''}
                        onChange={e => updateGateway(current => ({
                          ...current,
                          providers: current.providers.map(item => item.id === selectedProvider.id ? { ...item, api_key: e.target.value } : item),
                        }))}
                        className="w-full bg-slate-900/60 border border-slate-700/60 rounded-lg text-sm text-slate-200 px-3 py-2 focus:outline-none focus:border-indigo-500/60"
                        placeholder="sk-..."
                      />
                    </Field>

                    <Field label="Base URL">
                      <input
                        type="text"
                        value={selectedProvider.base_url || ''}
                        onChange={e => updateGateway(current => ({
                          ...current,
                          providers: current.providers.map(item => item.id === selectedProvider.id ? { ...item, base_url: e.target.value } : item),
                        }))}
                        className="w-full bg-slate-900/60 border border-slate-700/60 rounded-lg text-sm text-slate-200 px-3 py-2 focus:outline-none focus:border-indigo-500/60"
                        placeholder="https://api.openai.com/v1"
                      />
                    </Field>
                  </div>
                )}

                {selectedProvider.provider_type === 'codex_cli' && (
                  <Field label="Codex 命令">
                    <input
                      type="text"
                      value={selectedProvider.command || 'codex'}
                      onChange={e => updateGateway(current => ({
                        ...current,
                        providers: current.providers.map(item => item.id === selectedProvider.id ? { ...item, command: e.target.value } : item),
                      }))}
                      className="w-full bg-slate-900/60 border border-slate-700/60 rounded-lg text-sm text-slate-200 px-3 py-2 focus:outline-none focus:border-indigo-500/60"
                      placeholder="codex"
                    />
                  </Field>
                )}

                <p className="text-xs text-slate-500 leading-relaxed">
                  {formatTestMeta(selectedProvider)}
                  {selectedProvider.last_test_message ? ` · ${selectedProvider.last_test_message}` : ''}
                </p>
              </div>
            ) : (
              <p className="text-sm text-slate-500">暂无可用 Provider。</p>
            )}
          </div>

          <div className="rounded-xl border border-slate-800 bg-slate-950/40 p-4">
            <div className="mb-4">
              <p className="text-sm font-medium text-slate-100">任务绑定</p>
              <p className="text-xs text-slate-500 mt-1">按任务挑模型。`Provider` 决定走 `Codex` 还是 `OpenAPI`，`Brand` 和 `Model` 只显示这个任务当前可用的候选。</p>
            </div>

            <div className="grid grid-cols-1 gap-3">
              {taskSpecs.map(task => {
                const recommendedModel = task.recommended_model_id ? modelById[task.recommended_model_id] : null
                const binding = normalizeTaskBinding(taskBindings[task.id], task.recommended_model_id || '')
                const candidates = models.filter(model => model.supported_tasks.includes(task.id))
                const selectedModel = binding.model_id ? modelById[binding.model_id] || null : null
                const selectedModelProvider = selectedModel ? providerById[selectedModel.provider_id] : null

                const routeOptions = Array.from(new Set(
                  candidates.map(model => providerRoute(providerById[model.provider_id])),
                )) as ProviderRoute[]
                const currentRoute = routeOptions.includes(providerRoute(selectedModelProvider))
                  ? providerRoute(selectedModelProvider)
                  : (routeOptions[0] || 'open_api')

                const routeCandidates = candidates.filter(model => (
                  providerRoute(providerById[model.provider_id]) === currentRoute
                ))

                const brandProviderIds = Array.from(new Set(routeCandidates.map(model => model.provider_id)))
                const currentBrandProviderId = brandProviderIds.includes(selectedModel?.provider_id || '')
                  ? selectedModel?.provider_id || ''
                  : (brandProviderIds[0] || '')

                const brandCandidates = routeCandidates.filter(model => model.provider_id === currentBrandProviderId)
                const currentModelId = brandCandidates.some(model => model.id === binding.model_id)
                  ? binding.model_id
                  : (brandCandidates[0]?.id || '')

                return (
                  <div key={task.id} className="rounded-xl border border-slate-800 bg-slate-900/40 p-3.5">
                    <div className="flex items-start justify-between gap-3 mb-3">
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-slate-100">{task.label}</p>
                        <p className="text-xs text-slate-500 mt-1 leading-relaxed">{task.description}</p>
                      </div>
                      <div className="shrink-0 rounded-lg border border-slate-800 bg-slate-950/40 px-2 py-0.5 text-[11px] text-slate-400">
                        {task.category}
                      </div>
                    </div>

                    <div className="rounded-lg border border-slate-800/80 bg-slate-950/30 px-3 py-2 text-xs text-slate-500 mb-3">
                      系统推荐：<span className="text-slate-300">{recommendedModel?.label || task.recommended_model_id || '未设置'}</span>
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-2 2xl:grid-cols-5 gap-3">
                      <Field icon={<Cpu size={14} />} label="Provider">
                        <select
                          value={currentRoute}
                          onChange={e => {
                            const nextRoute = e.target.value as ProviderRoute
                            const nextModel = candidates.find(model => providerRoute(providerById[model.provider_id]) === nextRoute)
                            setTaskBinding(task.id, {
                              model_id: nextModel?.id || '',
                              reasoning_effort: binding.reasoning_effort,
                            })
                          }}
                          className="w-full bg-slate-900/60 border border-slate-700/60 rounded-lg text-[13px] text-slate-200 px-3 py-2 focus:outline-none focus:border-indigo-500/60"
                        >
                          {routeOptions.map(route => (
                            <option key={route} value={route}>{providerRouteLabel(route)}</option>
                          ))}
                        </select>
                      </Field>

                      <Field label="Task Type">
                        <select
                          value={task.task_type}
                          disabled
                          className="w-full bg-slate-900/40 border border-slate-800 rounded-lg text-[13px] text-slate-400 px-3 py-2 disabled:opacity-100"
                        >
                          <option value={task.task_type}>{TASK_TYPE_LABELS[task.task_type]}</option>
                        </select>
                      </Field>

                      <Field label="Model Brand">
                        <select
                          value={currentBrandProviderId}
                          onChange={e => {
                            const nextProviderId = e.target.value
                            const nextModel = routeCandidates.find(model => model.provider_id === nextProviderId)
                            setTaskBinding(task.id, {
                              model_id: nextModel?.id || '',
                              reasoning_effort: binding.reasoning_effort,
                            })
                          }}
                          className="w-full bg-slate-900/60 border border-slate-700/60 rounded-lg text-[13px] text-slate-200 px-3 py-2 focus:outline-none focus:border-indigo-500/60"
                        >
                          {brandProviderIds.map(providerId => (
                            <option key={providerId} value={providerId}>
                              {providerBrandLabel(providerById[providerId])}
                            </option>
                          ))}
                        </select>
                      </Field>

                      <Field label="Model">
                        <select
                          value={currentModelId}
                          onChange={e => setTaskBinding(task.id, {
                            model_id: e.target.value,
                            reasoning_effort: binding.reasoning_effort,
                          })}
                          className="w-full bg-slate-900/60 border border-slate-700/60 rounded-lg text-[13px] text-slate-200 px-3 py-2 focus:outline-none focus:border-indigo-500/60"
                        >
                          {brandCandidates.map(model => (
                            <option key={model.id} value={model.id}>
                              {model.label}
                            </option>
                          ))}
                        </select>
                      </Field>

                      <Field label="Thinking Effort">
                        <select
                          value={task.task_type === 'embedding' ? 'not_applicable' : binding.reasoning_effort}
                          disabled={task.task_type === 'embedding'}
                          onChange={e => setTaskBinding(task.id, {
                            model_id: currentModelId,
                            reasoning_effort: e.target.value as Required<ModelGatewayTaskBinding>['reasoning_effort'],
                          })}
                          className="w-full bg-slate-900/60 border border-slate-700/60 rounded-lg text-[13px] text-slate-200 px-3 py-2 disabled:opacity-60 focus:outline-none focus:border-indigo-500/60"
                        >
                          {task.task_type === 'embedding' ? (
                            <option value="not_applicable">不适用</option>
                          ) : (
                            <>
                              <option value="low">low</option>
                              <option value="medium">medium</option>
                              <option value="high">high</option>
                            </>
                          )}
                        </select>
                      </Field>

                      <div className="sm:col-span-2 2xl:col-span-5 rounded-lg border border-slate-800/80 bg-slate-950/30 px-3 py-2 text-xs text-slate-500 leading-relaxed">
                        当前路径：<span className="text-slate-300">{providerRouteLabel(currentRoute)}</span>
                        {' · '}
                        品牌：<span className="text-slate-300">{providerBrandLabel(providerById[currentBrandProviderId])}</span>
                        {currentModelId && (
                          <>
                            {' · '}
                            模型：<span className="text-slate-300">{modelById[currentModelId]?.upstream_model || currentModelId}</span>
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>

            <label className="flex items-start gap-3 bg-slate-900/40 border border-slate-800 rounded-lg p-4 cursor-pointer hover:border-slate-700 transition-colors mt-4">
              <input
                type="checkbox"
                checked={config.use_first_page_image ?? true}
                onChange={e => setConfig(current => ({ ...current, use_first_page_image: e.target.checked }))}
                className="mt-0.5 accent-indigo-500 w-4 h-4"
              />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-slate-200 font-medium flex items-center gap-2">
                  <ImageIcon size={13} className="text-slate-500" />
                  为论文抽取附带首页图像
                </p>
                <p className="text-xs text-slate-500 mt-1 leading-relaxed">
                  首页通常包含标题、作者和摘要，有助于抽取模型更准确地识别元信息。只有绑定到支持视觉输入的模型时才有意义。
                </p>
                {selectedPaperExtractModel && !selectedPaperExtractModel.supports_vision && (config.use_first_page_image ?? true) && (
                  <p className="text-xs text-amber-400 mt-2">
                    当前“论文抽取”绑定的模型未声明支持视觉，首页图像会被忽略。
                  </p>
                )}
              </div>
            </label>

            <p className="text-xs text-slate-500 mt-4 leading-relaxed">
              `thinking_effort` 当前会作用于 `Responses API` 和 `Codex CLI` 路线；`embedding` 任务不适用，`chat.completions` 型模型会忽略这个参数。
            </p>
          </div>
        </SettingGroup>

        <SettingGroup title="图谱" description="节点相似度连接阈值">
          <Field icon={<GitBranch size={14} />} label="相似度阈值" hint="低阈值产生更多连接，高阈值更精确">
            <div>
              <div className="flex items-center gap-3">
                <button
                  onClick={() => setConfig(current => ({ ...current, similarity_threshold: Math.max(0.4, Math.round(((current.similarity_threshold ?? 0.6) - 0.05) * 100) / 100) }))}
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
                    onChange={e => setConfig(current => ({ ...current, similarity_threshold: parseFloat(e.target.value) }))}
                    className="absolute inset-0 w-full accent-indigo-500 z-10"
                  />
                  <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 flex justify-between pointer-events-none px-[2px]">
                    {[0.4, 0.5, 0.6, 0.7, 0.8, 0.9].map(t => (
                      <span key={t} className="w-px h-2 bg-slate-700" />
                    ))}
                  </div>
                </div>
                <button
                  onClick={() => setConfig(current => ({ ...current, similarity_threshold: Math.min(0.9, Math.round(((current.similarity_threshold ?? 0.6) + 0.05) * 100) / 100) }))}
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

        <div className="my-10 flex items-center gap-3">
          <div className="flex-1 h-px bg-slate-800/80" />
          <span className="text-[10px] tracking-[0.18em] uppercase text-slate-600 font-semibold">维护操作</span>
          <div className="flex-1 h-px bg-slate-800/80" />
        </div>

        <SettingGroup title="图谱维护" description="调整阈值或重新处理" defaultExpanded={false}>
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
            desc="清空自动抽取的知识节点和边，将论文标记为未处理。手动新增的概念会保留；重新处理论文仍会调用大模型。"
            buttonLabel="清空并重置"
            icon={<Trash2 size={14} />}
            destructive
            onClick={async () => {
              if (!confirm('确认重置自动图谱？论文会被标记为未处理，需要重新调用大模型；手动概念会保留。')) return
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
  title, description, children, defaultExpanded = true,
}: {
  title: string
  description?: string
  children: React.ReactNode
  defaultExpanded?: boolean
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)

  return (
    <section className="mb-8">
      <div className="surface-card overflow-hidden">
        <button
          type="button"
          onClick={() => setExpanded(value => !value)}
          className="w-full text-left px-5 py-4 bg-slate-950/18 hover:bg-slate-950/32 transition-colors"
        >
          <div className="flex items-start justify-between gap-4">
            <div className="min-w-0">
              <h2 className="text-lg font-semibold text-slate-100 tracking-tight mb-1">{title}</h2>
              {description && <p className="panel-subtitle">{description}</p>}
            </div>
            <span className="shrink-0 mt-0.5 inline-flex items-center gap-1 text-xs text-slate-400">
              {expanded ? '收起' : '展开'}
              {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </span>
          </div>
        </button>
        {expanded && (
          <div className="border-t border-slate-800/70 px-5 py-5 space-y-4 bg-slate-950/10">
            {children}
          </div>
        )}
      </div>
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
  label: string
  desc: string
  buttonLabel: string
  icon: React.ReactNode
  onClick: () => void
  destructive?: boolean
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
