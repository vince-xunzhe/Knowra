import { t as tr } from '../i18n/catalog'
import { useLocale } from '../i18n/preferences'
import { Tags } from 'lucide-react'

export default function KnowledgePaperCategoryEditorButton({
  onClick,
  className = 'ml-auto inline-flex items-center gap-1.5 rounded-lg border border-indigo-500/40 bg-indigo-500/10 px-3 py-1.5 text-[12px] text-indigo-200 transition-colors hover:bg-indigo-500/20',
}: {
  onClick: () => void
  className?: string
}) {
  useLocale()
  return (
    <button
      type="button"
      data-testid="paper-category-editor"
      aria-label={tr("大类编辑")}
      title={tr("编辑论文大类：新增、重命名或删除。不会删除论文。")}
      onClick={onClick}
      className={className}
    >
      <Tags size={12} />
      {tr("大类编辑")}
    </button>
  )
}
