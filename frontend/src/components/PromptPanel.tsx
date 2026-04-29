import { useEffect, useMemo, useRef, useState } from 'react'
import { Save, RotateCcw, Hash, Loader2, Maximize2, X } from 'lucide-react'
import { getPrompt, updatePrompt, resetPrompt } from '../api/client'

// Prompt editor with two presentations:
//   1. Compact panel — lives in the Papers right column (~24rem wide).
//   2. Expanded modal  — full-screen overlay with a large textarea, line
//      number gutter, and a default-prompt reference panel. Triggered by
//      the maximize button.
//
// Both share the same `prompt` state, so unsaved edits flow seamlessly
// between the two views. The modal mounts ON TOP of the compact view; we
// don't unmount the compact panel so the user perceives the expand as an
// overlay rather than a navigation.

function estimateTokens(text: string): number {
  let cjk = 0, other = 0
  for (const ch of text) {
    if (/[㐀-鿿豈-﫿　-〿]/.test(ch)) cjk++
    else other++
  }
  return Math.round(cjk + other / 3.6)
}

export default function PromptPanel() {
  const [prompt, setPrompt] = useState('')
  const [defaultPrompt, setDefaultPrompt] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const compactRef = useRef<HTMLTextAreaElement>(null)
  const expandedRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    getPrompt()
      .then(r => {
        setPrompt(r.extraction_prompt)
        setDefaultPrompt(r.default_prompt)
      })
      .finally(() => setLoading(false))
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

  // Cmd/Ctrl+S in either textarea saves. ESC closes the modal if open.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
        const active = document.activeElement
        if (active === compactRef.current || active === expandedRef.current) {
          e.preventDefault()
          void handleSave()
        }
      } else if (e.key === 'Escape' && expanded) {
        setExpanded(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prompt, expanded])

  // Auto-focus the expanded textarea when the modal opens.
  useEffect(() => {
    if (expanded) {
      // requestAnimationFrame so the textarea exists in DOM by the time we
      // call .focus().
      requestAnimationFrame(() => expandedRef.current?.focus())
    }
  }, [expanded])

  const isDirty = prompt !== defaultPrompt
  const charCount = prompt.length
  const tokenEstimate = useMemo(() => estimateTokens(prompt), [prompt])
  const lineCount = useMemo(() => prompt.split('\n').length, [prompt])

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center text-slate-500 text-sm">
        <Loader2 size={14} className="animate-spin mr-2" /> 加载 Prompt…
      </div>
    )
  }

  return (
    <>
      {/* Compact view (lives in the Papers right column) */}
      <div className="flex flex-col flex-1 min-h-0 overflow-hidden">
        {/* Action row */}
        <div className="px-5 py-3 border-b border-slate-800/80 flex flex-wrap items-center gap-2">
          <button
            onClick={handleSave}
            disabled={saving}
            className="inline-flex items-center gap-1.5 text-xs font-medium text-white bg-indigo-500 hover:bg-indigo-400 px-3 py-1.5 rounded-lg transition-colors disabled:bg-slate-700 disabled:text-slate-400"
          >
            <Save size={12} />
            {saved ? '已保存 ✓' : saving ? '保存中…' : '保存'}
          </button>
          <button
            onClick={handleReset}
            className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200 hover:bg-slate-800/50 px-2 py-1.5 rounded-lg transition-colors"
            title="重置为默认 Prompt"
          >
            <RotateCcw size={11} /> 重置
          </button>
          <button
            onClick={() => setExpanded(true)}
            className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200 hover:bg-slate-800/50 px-2 py-1.5 rounded-lg transition-colors"
            title="在大窗口中编辑"
          >
            <Maximize2 size={11} /> 展开
          </button>
          {isDirty ? (
            <span className="ml-auto chip bg-indigo-500/15 text-indigo-200 border border-indigo-500/30 text-[10px]">
              已自定义
            </span>
          ) : (
            <span className="ml-auto chip bg-slate-800 text-slate-500 text-[10px]">默认</span>
          )}
        </div>

        {/* Stats strip */}
        <div className="px-5 py-2 border-b border-slate-800/80 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10.5px] text-slate-500 tabular-nums">
          <span>{charCount} 字符</span>
          <span className="text-slate-700">·</span>
          <span className="inline-flex items-center gap-0.5">
            <Hash size={9} className="text-slate-600" /> ~{tokenEstimate.toLocaleString()} tokens
          </span>
        </div>

        {/* Editor — font sized inline so Chrome's "minimum font size" setting
            can't silently upscale the textarea. Tailwind's text-[Npx] is a
            class-level rule that the browser policy can override; inline
            styles have higher CSS specificity and stick more reliably. */}
        <div className="flex-1 min-h-0 p-3">
          <textarea
            ref={compactRef}
            value={prompt}
            onChange={e => setPrompt(e.target.value)}
            spellCheck={false}
            className="w-full h-full bg-slate-950/60 border border-slate-800 rounded-xl text-slate-200 px-3 py-2.5 font-mono resize-none focus:outline-none focus:border-indigo-500/60 transition-colors"
            placeholder="输入 extraction prompt…"
            style={{
              fontFamily: '"SF Mono", Menlo, Monaco, Consolas, monospace',
              tabSize: 2,
              fontSize: '11px',
              lineHeight: '15px',
            }}
          />
        </div>
      </div>

      {/* Expanded modal */}
      {expanded && (
        <div
          className="fixed inset-0 z-[100] bg-black/70 backdrop-blur-sm flex items-center justify-center p-6 fade-in"
          onMouseDown={e => {
            // Click on backdrop only (not on the panel) closes the modal.
            if (e.target === e.currentTarget) setExpanded(false)
          }}
        >
          <div className="w-full max-w-6xl h-[88vh] bg-[#0f1117] border border-slate-800 rounded-2xl shadow-2xl flex flex-col overflow-hidden">
            {/* Header */}
            <div className="px-6 py-4 border-b border-slate-800 flex items-center gap-3">
              <div className="min-w-0">
                <p className="text-[10px] tracking-[0.18em] uppercase text-slate-500">全局 Prompt · 大窗口编辑</p>
                <p className="text-base text-white font-semibold mt-0.5">论文抽取指令</p>
              </div>

              <div className="ml-auto flex items-center gap-2">
                <span className="text-[11px] text-slate-500 tabular-nums">
                  {lineCount} 行 · {charCount} 字符 · ~{tokenEstimate.toLocaleString()} tokens
                </span>
                {isDirty ? (
                  <span className="chip bg-indigo-500/15 text-indigo-200 border border-indigo-500/30 text-[10px]">已自定义</span>
                ) : (
                  <span className="chip bg-slate-800 text-slate-500 text-[10px]">默认</span>
                )}
                <button
                  onClick={handleReset}
                  className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200 hover:bg-slate-800/50 px-2 py-1.5 rounded-lg transition-colors"
                  title="重置为默认 Prompt"
                >
                  <RotateCcw size={11} /> 重置
                </button>
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="inline-flex items-center gap-1.5 text-sm font-medium text-white bg-indigo-500 hover:bg-indigo-400 px-3 py-1.5 rounded-lg transition-colors disabled:bg-slate-700 disabled:text-slate-400"
                >
                  <Save size={13} />
                  {saved ? '已保存 ✓' : saving ? '保存中…' : '保存'}
                  <span className="hidden sm:inline text-[10px] text-indigo-200/70 font-mono ml-0.5">⌘S</span>
                </button>
                <button
                  onClick={() => setExpanded(false)}
                  className="text-slate-500 hover:text-slate-100 hover:bg-slate-800/60 rounded-lg p-1.5 transition-colors"
                  title="收起 (Esc)"
                >
                  <X size={16} />
                </button>
              </div>
            </div>

            {/* Body: editor + sidebar with default prompt */}
            <div className="flex-1 min-h-0 grid gap-4 p-5 lg:grid-cols-[minmax(0,1fr)_22rem]">
              {/* Editor with line-number gutter */}
              <div className="h-full relative rounded-xl border border-slate-800 bg-slate-950/60 focus-within:border-indigo-500/60 transition-colors overflow-hidden flex">
                <LineGutter lines={lineCount} />
                <textarea
                  ref={expandedRef}
                  value={prompt}
                  onChange={e => setPrompt(e.target.value)}
                  spellCheck={false}
                  className="flex-1 bg-transparent text-[13px] text-slate-200 px-4 py-4 font-mono leading-6 resize-none focus:outline-none placeholder:text-slate-600"
                  placeholder="输入 extraction prompt…"
                  style={{ fontFamily: '"SF Mono", Menlo, Monaco, Consolas, monospace', tabSize: 2 }}
                />
              </div>

              {/* Sidebar */}
              <aside className="space-y-3 overflow-y-auto pr-1 hidden lg:block">
                <div className="surface-card p-4">
                  <p className="section-label mb-2">默认 Prompt 参考</p>
                  <pre className="text-[11px] text-slate-400 whitespace-pre-wrap break-words font-mono leading-relaxed max-h-[50vh] overflow-y-auto">
                    {defaultPrompt.slice(0, 3000)}
                    {defaultPrompt.length > 3000 ? '\n…' : ''}
                  </pre>
                </div>
              </aside>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

function LineGutter({ lines }: { lines: number }) {
  // Render-only gutter; for a "vibe-coded" prompt editor we don't bother
  // syncing scroll with the textarea — the content fits within a few hundred
  // lines and scroll desync is a minor cosmetic issue when scrolling far.
  return (
    <div
      className="select-none text-right text-[12px] leading-6 font-mono text-slate-600 py-4 pl-3 pr-2 bg-slate-950/80 border-r border-slate-800/70 overflow-hidden tabular-nums shrink-0"
      style={{ minWidth: '3rem', fontFamily: '"SF Mono", Menlo, Monaco, Consolas, monospace' }}
    >
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i}>{i + 1}</div>
      ))}
      <div className="h-10" />
    </div>
  )
}
