import { useCallback, useEffect, useState } from 'react'
import { X, Loader2, Save, Eraser, RotateCcw } from 'lucide-react'
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
 * Modal editor for the system prompt that drives the promotion LLM stage.
 *
 * The stored value defaults to empty — when empty, the backend skips the
 * LLM call and only runs heuristic. The editor surfaces the built-in
 * suggested template via a "插入默认模板" button so users have something
 * concrete to riff on instead of staring at an empty textarea.
 */
export default function PromotionPromptEditor({ open, onClose, onSaved }: Props) {
  const [prompt, setPrompt] = useState('')
  const [defaultTemplate, setDefaultTemplate] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savedToast, setSavedToast] = useState(false)

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
    if (open) void load()
  }, [open, load])

  const handleSave = useCallback(async () => {
    setSaving(true)
    setError(null)
    try {
      const result = await updatePromotionPrompt(prompt)
      onSaved?.(result.prompt)
      setSavedToast(true)
      setTimeout(() => setSavedToast(false), 1800)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }, [prompt, onSaved])

  const handleInsertDefault = useCallback(() => {
    setPrompt(defaultTemplate)
  }, [defaultTemplate])

  const handleClear = useCallback(() => {
    setPrompt('')
  }, [])

  if (!open) return null

  const isEmpty = prompt.trim().length === 0
  const charCount = prompt.length

  return (
    <div className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm flex items-center justify-center p-6">
      <div className="w-full max-w-2xl max-h-[80vh] bg-[#0f1117] border border-slate-800 rounded-2xl shadow-2xl flex flex-col">
        <header className="px-5 py-4 border-b border-slate-800 flex items-start gap-3">
          <div className="flex-1 min-w-0">
            <h2 className="text-base font-semibold text-white">编辑剔除提示词</h2>
            <p className="text-[12px] text-slate-500 mt-1 leading-relaxed">
              这是「自动剔除」时发给 Agent 的 system prompt。<b>留空 → 只跑启发式，不调 Agent</b>。
              想用 Agent 但没思路？点「插入默认模板」开始。
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-slate-500 hover:text-white p-1.5 -mr-1 rounded-lg hover:bg-slate-800/60"
          >
            <X size={16} />
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loading ? (
            <div className="py-12 flex items-center justify-center text-slate-500 text-[12px]">
              <Loader2 size={12} className="animate-spin mr-2" /> 加载中…
            </div>
          ) : (
            <>
              <div className="flex items-center gap-2 mb-2">
                <span className="text-[11px] text-slate-500">
                  {isEmpty ? (
                    <span className="text-amber-300">空 · Agent 将被跳过</span>
                  ) : (
                    <span className="text-emerald-300">已配置 · {charCount} 字符</span>
                  )}
                </span>
                <button
                  onClick={handleInsertDefault}
                  disabled={!defaultTemplate || prompt.trim() === defaultTemplate.trim()}
                  className="ml-auto inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-md border border-slate-700 bg-slate-900 hover:bg-slate-800 text-slate-300 disabled:opacity-50 transition-colors"
                  title={
                    prompt.trim() === defaultTemplate.trim()
                      ? '当前已经是默认模板'
                      : '把编辑框内容重置为内置默认模板（保存后生效）'
                  }
                >
                  <RotateCcw size={11} />
                  恢复默认
                </button>
                <button
                  onClick={handleClear}
                  disabled={isEmpty}
                  className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-md border border-slate-700 bg-slate-900 hover:bg-slate-800 text-slate-300 disabled:opacity-50 transition-colors"
                  title="清空提示词，下次自动剔除将跳过 Agent"
                >
                  <Eraser size={11} />
                  清空
                </button>
              </div>
              <textarea
                value={prompt}
                onChange={e => setPrompt(e.target.value)}
                placeholder="例如：你是个人 LLM 知识库的概念精选助手……（留空则不调 Agent）"
                className="w-full h-[28rem] resize-none px-3 py-2.5 text-[13px] leading-6 bg-slate-950 border border-slate-800 rounded-lg text-slate-200 placeholder-slate-600 font-mono focus:outline-none focus:border-slate-700"
                spellCheck={false}
              />
              <p className="mt-2 text-[10.5px] text-slate-600 leading-relaxed">
                后端会在你的内容末尾自动追加输出格式约束（JSON schema），不需要在提示词里重复声明。
              </p>
            </>
          )}
        </div>

        <footer className="px-5 py-3 border-t border-slate-800 flex items-center gap-3">
          {error && (
            <span className="text-[11px] text-rose-300 flex-1">{error}</span>
          )}
          {savedToast && !error && (
            <span className="text-[11px] text-emerald-300 flex-1">已保存 ✓</span>
          )}
          {!error && !savedToast && <span className="flex-1" />}
          <button
            onClick={onClose}
            className="text-[12px] px-3 py-1.5 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800/60 transition-colors"
          >
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving || loading}
            className="inline-flex items-center gap-1.5 text-[12px] font-medium px-3 py-1.5 rounded-lg bg-indigo-500 hover:bg-indigo-400 text-white disabled:opacity-50 transition-colors"
          >
            {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
            保存
          </button>
        </footer>
      </div>
    </div>
  )
}
