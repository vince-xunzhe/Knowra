import { useEffect, useState } from 'react'
import { Loader2, RefreshCw, Sparkles, X } from 'lucide-react'
import { listDomainFrameworks, type DomainFrameworkOption, type DomainFrameworkSelection } from '../api/domains'
import { localApiErrorMessage } from '../api/errors'
import { t as tr } from '../i18n/catalog'

/** A selection is scoped to this open dialog and its board, never a global preference. */
export default function DomainFrameworkDialog({
  boardId, busy, error, onClearError, onClose, onStart,
}: {
  boardId: string
  busy: boolean
  error: string
  onClearError: () => void
  onClose: () => void
  onStart: (framework?: DomainFrameworkSelection) => void
}) {
  const [options, setOptions] = useState<DomainFrameworkOption[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [reload, setReload] = useState(0)
  const selected = options.find(option => option.boardId === selectedId)
  useEffect(() => {
    const controller = new AbortController()
    void listDomainFrameworks(boardId, controller.signal).then(result => {
      if (!controller.signal.aborted) setOptions(result)
    }).catch(error => {
      if (!controller.signal.aborted) {
        setLoadError(localApiErrorMessage(error, '识途框架暂时无法读取，请刷新框架，或不使用框架继续。'))
      }
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false)
    })
    return () => controller.abort()
  }, [boardId, reload])
  const refresh = () => {
    if (busy) return
    setSelectedId('')
    setOptions([])
    setLoading(true)
    setLoadError('')
    onClearError()
    setReload(value => value + 1)
  }
  return <div className="domain-modal-backdrop" onClick={() => { if (!busy) onClose() }}>
    <form
      role="dialog"
      aria-modal="true"
      aria-label={tr('Agent 代连')}
      className="domain-modal domain-framework-dialog"
      onClick={event => event.stopPropagation()}
      onSubmit={event => {
        event.preventDefault()
        if (busy || (selectedId && !selected)) return
        onStart(selected ? { boardId: selected.boardId, version: selected.version } : undefined)
      }}
    >
      <header>
        <div>
          <h2>{tr('Agent 代连')}</h2>
          <p>{tr('为当前论文选择一个识途框架，辅助理解研究问题与发展分支。')}</p>
        </div>
        <button type="button" disabled={busy} aria-label={tr('关闭')} onClick={onClose}><X size={18} /></button>
      </header>
      <label className="domain-framework-label">
        <span>{tr('识途参考框架')}</span>
        <select autoFocus aria-label={tr('识途参考框架')} disabled={busy} value={selectedId} onChange={event => {
          setSelectedId(event.target.value)
          onClearError()
        }}>
          <option value="">{tr('不使用框架（仅依据论文材料）')}</option>
          {options.map(option => <option key={option.boardId} value={option.boardId}>
            {option.name}{option.sameCategory ? ` · ${tr('同领域')}` : ''}
          </option>)}
        </select>
      </label>
      <div className="domain-framework-status">
        <span aria-live="polite">{loading ? tr('正在读取已保存的识途框架…')
          : !loadError && !options.length ? tr('暂无可用框架，可仅依据论文材料生成。')
          : tr('优先显示同领域框架；仅使用识途已保存的内容。')}</span>
        <button type="button" disabled={busy || loading} onClick={refresh}><RefreshCw size={14} />{tr('刷新框架')}</button>
      </div>
      {selected && <div className="domain-framework-selection">
        <strong>{selected.name}</strong>
        <p>{tr('版本 {0} · {1} 篇参考论文', { 0: selected.version.slice(0, 12), 1: selected.paperCount })}</p>
        {!selected.sameCategory && <p>{tr('此框架来自其他领域；不适用或矛盾之处会作为分析提示，不强行归类。')}</p>}
      </div>}
      <p className="domain-framework-notice">{tr('框架仅作分析依据，连线仍须当前论文的回顾、Wiki 或原文证据。先预览，再由你应用；保留手工整理和锁定项。')}</p>
      <p>{tr('本次使用固定快照，不会随识途后续修改同步，也不会修改识途。')}</p>
      {(error || loadError) && <div className="domain-error" role="alert">{tr(error || loadError)}</div>}
      <footer>
        <button type="button" disabled={busy} onClick={onClose}>{tr('取消')}</button>
        <button className="domain-primary" type="submit" disabled={busy || Boolean(selectedId && !selected)}>
          {busy ? <Loader2 size={16} className="spin" /> : <Sparkles size={16} />}{tr('生成代连预览')}
        </button>
      </footer>
    </form>
  </div>
}
