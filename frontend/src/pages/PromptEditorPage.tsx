import { useEffect, useMemo, useRef, useState } from 'react'
import { Save, RotateCcw, FileText, Info, Hash } from 'lucide-react'
import { getPrompt, updatePrompt, resetPrompt } from '../api/client'

const REQUIRED_FIELDS = [
  'title',
  'authors',
  'techniques',
  'datasets',
  'keywords',
  'key_findings',
  'problem_area',
]

// Rough token estimator: CJK chars ≈ 1 token, latin words ≈ 1 token / ~4 chars
function estimateTokens(text: string): number {
  let cjk = 0, other = 0
  for (const ch of text) {
    if (/[\u3400-\u9fff\uf900-\ufaff\u3000-\u303f]/.test(ch)) cjk++
    else other++
  }
  return Math.round(cjk + other / 3.6)
}

export default function PromptEditorPage() {
  const [prompt, setPrompt] = useState('')
  const [defaultPrompt, setDefaultPrompt] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const gutterRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    getPrompt().then(r => {
      setPrompt(r.extraction_prompt)
      setDefaultPrompt(r.default_prompt)
      setLoading(false)
    }).catch(() => setLoading(false))
  }, [])

  const handleSave = async () => {
    setSaving(true)
    try {
      await updatePrompt(prompt)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } finally {
      setSaving(false)
    }
  }

  const handleReset = async () => {
    if (!confirm('确认重置为默认 prompt？当前修改会丢失。')) return
    const r = await resetPrompt()
    setPrompt(r.extraction_prompt)
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  // Cmd/Ctrl + S
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
        e.preventDefault()
        handleSave()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prompt])

  const isDirty = prompt !== defaultPrompt
  const charCount = prompt.length
  const lineCount = prompt.split('\n').length
  const tokenEstimate = useMemo(() => estimateTokens(prompt), [prompt])

  // Click a field chip → find first occurrence in prompt and select it
  const focusField = (field: string) => {
    const ta = textareaRef.current
    if (!ta) return
    const idx = prompt.indexOf(`"${field}"`)
    const target = idx >= 0 ? idx : prompt.indexOf(field)
    if (target < 0) return
    ta.focus()
    ta.setSelectionRange(target, target + field.length + 2)
    // Scroll roughly to that line
    const lineNo = prompt.slice(0, target).split('\n').length - 1
    const lineHeight = 28
    ta.scrollTop = Math.max(0, lineNo * lineHeight - ta.clientHeight / 2)
  }

  const syncScroll = () => {
    if (textareaRef.current && gutterRef.current) {
      gutterRef.current.scrollTop = textareaRef.current.scrollTop
    }
  }

  if (loading) return <div className="p-10 text-sm text-slate-500">加载中…</div>

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <header className="bg-[#0f1117] border-b border-slate-800/80 px-6 py-4">
        <div className="flex flex-wrap items-start gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <FileText size={15} className="text-indigo-400" />
              <h1 className="text-lg font-semibold text-white tracking-tight">Prompt 编辑器</h1>
            </div>
            <p className="text-sm text-slate-500 mt-1">
              调整论文抽取指令，让结构化输出更稳定、更符合你的知识图谱需求。
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="rounded-xl border border-slate-800 bg-slate-900/60 px-3 py-1.5 text-slate-500 tabular-nums">
              {lineCount} 行 · {charCount} 字符
            </span>
            <span
              className="rounded-xl border border-slate-800 bg-slate-900/60 px-3 py-1.5 text-slate-500 tabular-nums inline-flex items-center gap-1"
              title="粗略估算（CJK 1 token/字, 英文 ~3.6 字符/token）"
            >
              <Hash size={11} className="text-slate-600" />
              ~{tokenEstimate.toLocaleString()} tokens
            </span>
            {isDirty ? (
              <span className="chip bg-indigo-500/15 text-indigo-200 border border-indigo-500/30 text-xs">已自定义</span>
            ) : (
              <span className="chip bg-slate-800 text-slate-500 text-xs">默认</span>
            )}
          </div>

          <div className="ml-auto flex flex-wrap items-center gap-2">
            <button
              onClick={handleReset}
              className="inline-flex items-center gap-1.5 text-sm text-slate-400 hover:text-slate-200 hover:bg-slate-800/50 px-3 py-2 rounded-xl transition-colors"
            >
              <RotateCcw size={13} /> 重置默认
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className="inline-flex items-center gap-1.5 text-sm font-medium text-white bg-indigo-500 hover:bg-indigo-400 px-4 py-2 rounded-xl transition-colors disabled:bg-slate-700 disabled:text-slate-400 shadow-lg shadow-indigo-500/20"
            >
              <Save size={14} /> {saved ? '已保存 ✓' : saving ? '保存中…' : '保存'}
              <span className="hidden sm:inline text-[10px] text-indigo-200/70 font-mono ml-1">⌘S</span>
            </button>
          </div>
        </div>
      </header>

      {/* Info banner */}
      <div className="px-6 py-4 bg-slate-900/40 border-b border-slate-800/60">
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(22rem,0.8fr)]">
          <div className="surface-card p-4">
            <div className="flex items-start gap-2.5">
              <Info size={14} className="text-indigo-400 mt-0.5 shrink-0" />
              <div>
                <p className="panel-title">编辑原则</p>
                <p className="panel-subtitle mt-1">
                  这段 Prompt 会作为指令发送给模型，配合通过 file_search 上传的 PDF 一起使用。
                  请始终让模型只返回合法 JSON，不要附带解释性文字或 Markdown 代码块。
                </p>
              </div>
            </div>
          </div>

          <div className="surface-card p-4">
            <p className="section-label mb-2">关键字段（点击定位）</p>
            <div className="flex flex-wrap gap-1.5">
              {REQUIRED_FIELDS.map(field => {
                const present = prompt.includes(`"${field}"`)
                return (
                  <button
                    key={field}
                    onClick={() => focusField(field)}
                    className={`chip text-xs cursor-pointer transition-colors ${
                      present
                        ? 'bg-slate-800/90 text-slate-300 border border-slate-700/50 hover:border-indigo-500/50 hover:text-indigo-200'
                        : 'bg-amber-500/10 text-amber-300 border border-amber-500/30 hover:bg-amber-500/20'
                    }`}
                    title={present ? '点击定位到 prompt 中' : '当前 prompt 中未出现此字段'}
                  >
                    {field}
                    {!present && <span className="ml-1 text-[10px]">!</span>}
                  </button>
                )
              })}
            </div>
            <p className="panel-subtitle mt-3">
              这些字段缺失时，论文回顾和知识节点构建都会变得不稳定。
            </p>
          </div>
        </div>
      </div>

      {/* Editor */}
      <div className="flex-1 min-h-0 overflow-hidden p-6 pt-5">
        <div className="grid h-full gap-5 xl:grid-cols-[minmax(0,1fr)_24rem]">
          <div className="h-full relative rounded-2xl border border-slate-800 bg-slate-950/60 focus-within:border-indigo-500/60 transition-colors overflow-hidden flex">
            {/* Line number gutter */}
            <div
              ref={gutterRef}
              className="select-none text-right text-[12px] leading-7 font-mono text-slate-600 py-5 pl-3 pr-2 bg-slate-950/80 border-r border-slate-800/70 overflow-hidden tabular-nums"
              style={{ minWidth: '3rem', fontFamily: '"SF Mono", Menlo, Monaco, Consolas, monospace' }}
            >
              {Array.from({ length: lineCount }).map((_, i) => (
                <div key={i}>{i + 1}</div>
              ))}
              <div className="h-10" />
            </div>
            <textarea
              ref={textareaRef}
              value={prompt}
              onChange={e => setPrompt(e.target.value)}
              onScroll={syncScroll}
              spellCheck={false}
              className="flex-1 bg-transparent text-[13px] text-slate-200 px-4 py-5 font-mono leading-7 resize-none focus:outline-none placeholder:text-slate-600"
              placeholder="输入 extraction prompt…"
              style={{ fontFamily: '"SF Mono", Menlo, Monaco, Consolas, monospace', tabSize: 2 }}
            />
          </div>

          <aside className="space-y-4 overflow-y-auto pr-1">
            <div className="surface-card p-4">
              <p className="section-label mb-2">修改建议</p>
              <ul className="space-y-2 text-[13px] text-slate-300 leading-relaxed">
                <li>· 明确输出字段、数据类型和缺失值策略</li>
                <li>· 避免让模型返回"解释过程"或额外备注</li>
                <li>· 想增强稳定性时，优先补充示例而不是堆口语化要求</li>
                <li>· 字段名必须与图谱节点类型匹配，否则回顾页会失效</li>
              </ul>
            </div>

            <div className="surface-card p-4">
              <p className="section-label mb-2">默认 Prompt 片段</p>
              <pre className="text-[12px] text-slate-400 whitespace-pre-wrap break-words font-mono leading-relaxed max-h-80 overflow-y-auto">
                {defaultPrompt.slice(0, 1200)}
                {defaultPrompt.length > 1200 ? '\n…' : ''}
              </pre>
            </div>
          </aside>
        </div>
      </div>
    </div>
  )
}
