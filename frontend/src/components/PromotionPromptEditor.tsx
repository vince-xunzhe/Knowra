import { useCallback, useEffect, useState } from 'react'
import { X, Loader2, Save, Eraser, RotateCcw, Pencil } from 'lucide-react'
import {
  getPromotionPrompt,
  updatePromotionPrompt,
} from '../api/client'

interface Props {
  open: boolean
  onClose: () => void
  /** Called after a successful save with the new value, so the host can
   *  refresh derived UI state (e.g. "use_llm" hint). */
  onSaved?: (prompt: string) => void
}

/**
 * Modal viewer / editor for the system prompt that drives the promotion
 * LLM stage.
 *
 * Two-mode design:
 *   - View mode (default on open): textarea is read-only, only "编辑" CTA
 *     is visible. Prevents accidental edits and lets users copy text.
 *   - Edit mode: typing enabled, mutator buttons (恢复默认 / 清空) appear
 *     in the toolbar, primary footer actions become 取消 / 保存.
 *
 * The 取消 path snapshots the prompt at edit-mode entry and restores it,
 * so a half-typed change can be cleanly discarded without re-fetching.
 */
export default function PromotionPromptEditor({ open, onClose, onSaved }: Props) {
  const [prompt, setPrompt] = useState('')
  const [defaultTemplate, setDefaultTemplate] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savedToast, setSavedToast] = useState(false)
  const [isEditing, setIsEditing] = useState(false)
  // Snapshot of the prompt when edit-mode was entered, used by 取消 to
  // bail out of a half-typed change without a backend round-trip.
  const [snapshot, setSnapshot] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getPromotionPrompt()
      setPrompt(data.prompt)
      setDefaultTemplate(data.default_template)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) {
      // Each open starts fresh in view mode, regardless of where the
      // user left off last time.
      setIsEditing(false)
      void load()
    }
  }, [open, load])

  const handleSave = useCallback(async () => {
    setSaving(true)
    setError(null)
    try {
      const result = await updatePromotionPrompt(prompt)
      onSaved?.(result.prompt)
      setSavedToast(true)
      setIsEditing(false)
      setTimeout(() => setSavedToast(false), 1800)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }, [prompt, onSaved])

  const handleEnterEdit = useCallback(() => {
    setSnapshot(prompt)
    setIsEditing(true)
    setError(null)
    setSavedToast(false)
  }, [prompt])

  const handleCancelEdit = useCallback(() => {
    setPrompt(snapshot)
    setIsEditing(false)
    setError(null)
  }, [snapshot])

  const handleInsertDefault = useCallback(() => {
    setPrompt(defaultTemplate)
  }, [defaultTemplate])

  const handleClear = useCallback(() => {
    setPrompt('')
  }, [])

  if (!open) return null

  const isEmpty = prompt.trim().length === 0
  const charCount = prompt.length
  const isDefault =
    defaultTemplate.length > 0 && prompt.trim() === defaultTemplate.trim()

  const statusDot = isEmpty
    ? 'bg-amber-400'
    : isDefault
      ? 'bg-slate-400'
      : 'bg-emerald-400'
  const statusText = isEmpty
    ? '空白 · 跳过 Agent'
    : isDefault
      ? '默认模板'
      : '已自定义'

  return (
    <div className="fixed inset-0 z-40 bg-black/65 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="w-full max-w-[44rem] max-h-[86vh] bg-[#0d1016] border border-slate-800/80 rounded-xl shadow-[0_24px_70px_rgba(2,6,23,0.7)] flex flex-col overflow-hidden">
        {/* Header — slim title strip */}
        <header className="px-4 py-2.5 border-b border-slate-800/70 flex items-center gap-2">
          <span className="text-[10px] tracking-[0.12em] uppercase text-indigo-300/70 font-mono">
            概念精选
          </span>
          <span className="text-slate-700">/</span>
          <h2 className="text-[13px] font-semibold text-white tracking-tight">
            剔除提示词
          </h2>
          {isEditing && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/15 text-indigo-200 border border-indigo-500/30">
              编辑中
            </span>
          )}
          <button
            onClick={onClose}
            className="ml-auto text-slate-500 hover:text-slate-200 p-1 rounded hover:bg-slate-800/60 transition-colors"
            title="关闭"
          >
            <X size={13} />
          </button>
        </header>

        {/* Toolbar — status + mode-aware mutators */}
        <div className="px-4 py-2 border-b border-slate-800/70 flex items-center gap-2 flex-wrap">
          <span className="inline-flex items-center gap-1.5 text-[10.5px] text-slate-300">
            <span className={`w-1.5 h-1.5 rounded-full ${statusDot}`} />
            {statusText}
          </span>
          <span className="text-[10px] text-slate-600 tabular-nums">
            · {charCount} 字符
          </span>
          {/* Mutator buttons live in the toolbar only when actually editable
              — keeps the view-mode chrome minimal. */}
          {isEditing && (
            <div className="ml-auto flex items-center gap-1.5">
              <button
                onClick={handleInsertDefault}
                disabled={!defaultTemplate || isDefault}
                className="inline-flex items-center gap-1 text-[10.5px] px-1.5 py-0.5 rounded border border-slate-700 bg-slate-900 hover:bg-slate-800 text-slate-300 disabled:opacity-40 transition-colors"
                title={
                  isDefault
                    ? '当前已经是默认模板'
                    : '把编辑框内容重置为内置默认模板（保存后生效）'
                }
              >
                <RotateCcw size={9} />
                恢复默认
              </button>
              <button
                onClick={handleClear}
                disabled={isEmpty}
                className="inline-flex items-center gap-1 text-[10.5px] px-1.5 py-0.5 rounded border border-slate-700 bg-slate-900 hover:bg-slate-800 text-slate-300 disabled:opacity-40 transition-colors"
                title="清空提示词，下次自动剔除将跳过 Agent"
              >
                <Eraser size={9} />
                清空
              </button>
            </div>
          )}
        </div>

        {/* Textarea — read-only in view mode, editable after 编辑 */}
        <div
          className={`flex-1 min-h-0 overflow-hidden transition-colors ${
            isEditing ? 'bg-[#070912]' : 'bg-[#0a0d14]'
          }`}
        >
          {loading ? (
            <div className="h-full flex items-center justify-center text-slate-500 text-[11px]">
              <Loader2 size={11} className="animate-spin mr-2" /> 加载中…
            </div>
          ) : (
            <textarea
              value={prompt}
              onChange={e => setPrompt(e.target.value)}
              readOnly={!isEditing}
              placeholder={
                isEditing
                  ? '例如：你是个人 LLM 知识库的概念精选助手……（留空则不调 Agent）'
                  : '提示词为空 — 点右下角「编辑」开始填写'
              }
              className={`block w-full h-full min-h-[22rem] resize-none bg-transparent px-4 py-3 text-[11px] leading-[1.85] placeholder-slate-700 focus:outline-none ${
                isEditing
                  ? 'text-slate-100 cursor-text'
                  : 'text-slate-300 cursor-default selection:bg-indigo-500/30'
              }`}
              spellCheck={false}
              style={{
                fontFamily:
                  '"SF Mono", "JetBrains Mono", Menlo, Consolas, "PingFang SC", monospace',
                tabSize: 2,
              }}
            />
          )}
        </div>

        {/* Footer — primary actions depend on mode */}
        <footer className="px-4 py-2 border-t border-slate-800/70 flex items-center gap-3">
          <span className="flex-1 min-w-0 text-[10px] text-slate-500 truncate">
            {error ? (
              <span className="text-rose-300">{error}</span>
            ) : savedToast ? (
              <span className="text-emerald-300">✓ 已保存，下次自动剔除将使用此提示词</span>
            ) : isEditing ? (
              '后端会自动追加 JSON 输出协议，无需自行声明。'
            ) : (
              '只读预览。点「编辑」开始修改。'
            )}
          </span>
          {isEditing ? (
            <>
              <button
                onClick={handleCancelEdit}
                disabled={saving}
                className="text-[11px] px-2.5 py-1 rounded text-slate-400 hover:text-slate-200 hover:bg-slate-800/60 disabled:opacity-50 transition-colors"
              >
                取消
              </button>
              <button
                onClick={handleSave}
                disabled={saving || loading}
                className="inline-flex items-center gap-1.5 text-[11px] font-medium px-2.5 py-1 rounded bg-indigo-500 hover:bg-indigo-400 text-white disabled:opacity-50 transition-colors"
              >
                {saving ? <Loader2 size={11} className="animate-spin" /> : <Save size={11} />}
                保存
              </button>
            </>
          ) : (
            <button
              onClick={handleEnterEdit}
              disabled={loading}
              className="inline-flex items-center gap-1.5 text-[11px] font-medium px-2.5 py-1 rounded bg-indigo-500 hover:bg-indigo-400 text-white disabled:opacity-50 transition-colors"
            >
              <Pencil size={11} />
              编辑
            </button>
          )}
        </footer>
      </div>
    </div>
  )
}
