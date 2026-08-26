import { useState } from 'react'
import { Check, Files, FolderOpen, Loader2, X } from 'lucide-react'
import type { DuplicatePaperFile } from '../api/client'

interface RevealResult {
  path: string
  selected: boolean
  file_manager: string
}

interface Props {
  items: DuplicatePaperFile[]
  selectedPath: string | null
  onSelect: (path: string) => void
  onReveal: (path: string) => Promise<RevealResult>
  onClose: () => void
}

const REASON_LABELS: Record<DuplicatePaperFile['reason'], string> = {
  same_arxiv_id: 'arXiv ID 相同',
  same_content: '文件内容完全相同',
}

export default function DuplicateFilesModal({
  items,
  selectedPath,
  onSelect,
  onReveal,
  onClose,
}: Props) {
  const [locating, setLocating] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const selected = items.find(item => item.path === selectedPath) || items[0]

  if (!selected) return null

  const reveal = async () => {
    setLocating(true)
    setError(null)
    setNotice(null)
    try {
      const result = await onReveal(selected.path)
      setNotice(
        result.selected
          ? `已在 ${result.file_manager} 中选中该文件`
          : `已在 ${result.file_manager} 中打开所在目录`,
      )
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setLocating(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/65 p-5 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby="duplicate-files-title"
      onMouseDown={event => {
        if (event.target === event.currentTarget) onClose()
      }}
    >
      <div className="flex max-h-[76vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-amber-500/30 bg-[#0d1119] shadow-2xl shadow-black/50">
        <header className="flex items-start gap-3 border-b border-slate-800 px-5 py-4">
          <span className="mt-0.5 rounded-lg border border-amber-400/25 bg-amber-400/10 p-2 text-amber-300">
            <Files size={17} />
          </span>
          <div className="min-w-0 flex-1">
            <h2 id="duplicate-files-title" className="text-[15px] font-semibold text-slate-100">
              发现 {items.length} 篇重复论文
            </h2>
            <p className="mt-1 text-[12px] leading-relaxed text-slate-400">
              已跳过这些文件，没有重复写入数据库。默认选中扫描路径下的第一项。
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded-md p-1.5 text-slate-500 hover:bg-slate-800 hover:text-slate-200"
            aria-label="关闭重复文件列表"
          >
            <X size={16} />
          </button>
        </header>

        <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <div className="min-h-0 overflow-y-auto border-r border-slate-800 p-3" role="menu">
            {items.map((item, index) => {
              const active = item.path === selected.path
              return (
                <button
                  key={item.path}
                  role="menuitem"
                  onClick={() => {
                    onSelect(item.path)
                    setNotice(null)
                    setError(null)
                  }}
                  className={`mb-1.5 flex w-full items-start gap-2 rounded-xl border px-3 py-2.5 text-left transition-colors ${
                    active
                      ? 'border-amber-400/45 bg-amber-400/10 text-slate-100'
                      : 'border-transparent bg-slate-900/55 text-slate-400 hover:border-slate-700 hover:bg-slate-800/70'
                  }`}
                >
                  <span className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border text-[9px] ${
                    active
                      ? 'border-amber-300 bg-amber-300 text-slate-950'
                      : 'border-slate-600 text-slate-500'
                  }`}>
                    {active ? <Check size={10} strokeWidth={3} /> : index + 1}
                  </span>
                  <span className="min-w-0">
                    <span className="block truncate text-[12px] font-medium" title={item.filename}>
                      {item.filename}
                    </span>
                    <span className="mt-0.5 block truncate text-[10.5px] text-slate-500" title={item.relative_path}>
                      {item.relative_path}
                    </span>
                  </span>
                </button>
              )
            })}
          </div>

          <div className="min-w-0 overflow-y-auto p-5">
            <div className="text-[10px] uppercase tracking-[0.16em] text-amber-300/75">
              {REASON_LABELS[selected.reason]}
            </div>
            <h3 className="mt-2 break-words text-[15px] font-semibold text-slate-100">
              {selected.filename}
            </h3>

            <div className="mt-4 space-y-3 text-[11.5px]">
              <Detail label="重复文件" value={selected.path} mono />
              <Detail
                label="对应入库论文"
                value={selected.matched_paper.title || selected.matched_paper.filename}
              />
              <Detail label="入库文件" value={selected.matched_paper.filename} mono />
            </div>

            {(notice || error) && (
              <p className={`mt-4 rounded-lg border px-3 py-2 text-[11px] ${
                error
                  ? 'border-rose-500/30 bg-rose-500/10 text-rose-200'
                  : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200'
              }`}>
                {error || notice}
              </p>
            )}

            <button
              onClick={() => void reveal()}
              disabled={locating}
              className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-xl border border-amber-400/40 bg-amber-400/10 px-4 py-2.5 text-[12px] font-medium text-amber-100 transition-colors hover:bg-amber-400/20 disabled:cursor-wait disabled:opacity-60"
            >
              {locating ? <Loader2 size={14} className="animate-spin" /> : <FolderOpen size={14} />}
              {locating ? '正在定位…' : '在文件管理器中显示'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function Detail({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div className="mb-1 text-slate-500">{label}</div>
      <div className={`break-all rounded-lg border border-slate-800 bg-slate-950/60 px-3 py-2 text-slate-300 ${mono ? 'font-mono text-[10.5px]' : ''}`}>
        {value}
      </div>
    </div>
  )
}
