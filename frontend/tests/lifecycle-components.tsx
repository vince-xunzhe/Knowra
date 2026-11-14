import { useState } from 'react'
import AskDrawer from '../src/components/AskDrawer'
import RejectedRescueModal from '../src/components/RejectedRescueModal'
import BackgroundTasks from '../src/components/BackgroundTasks'
import PapersPage from '../src/pages/PapersPage'
import KnowledgePaperCategoryEditorButton from '../src/components/KnowledgePaperCategoryEditorButton'
import CategoryComposer from '../src/components/CategoryComposer'
import { FileSearchConfirmationProvider } from '../src/hooks/useFileSearchConfirmation'

export function AskHarness() {
  const [open, setOpen] = useState(true)
  return <>
    <button onClick={() => setOpen(true)}>Open test drawer</button>
    <AskDrawer open={open} onClose={() => setOpen(false)} />
  </>
}

export function RescueHarness() {
  const [open, setOpen] = useState(true)
  const [recalled, setRecalled] = useState<number[]>([])
  return <>
    <button onClick={() => setOpen(true)}>Open test rescue</button>
    <output data-testid="recalled">{recalled.join(',')}</output>
    <RejectedRescueModal open={open} onClose={() => setOpen(false)} onRecalled={id => setRecalled(prev => [...prev, id])} />
  </>
}

export function BackgroundTasksHarness() {
  return (
    <FileSearchConfirmationProvider>
      <div className="flex min-h-screen bg-slate-950 text-slate-100">
        <nav className="flex w-[6.5rem] flex-col border-r border-slate-800 py-4" data-testid="tasks-nav">
          <div className="mt-auto">
            <BackgroundTasks />
          </div>
        </nav>
        <main className="flex-1 p-8" data-testid="tasks-outside">页面其他区域</main>
      </div>
    </FileSearchConfirmationProvider>
  )
}

export function LibraryPapersHarness() {
  const [opened, setOpened] = useState<number | null>(null)
  return (
    <FileSearchConfirmationProvider>
      <div className="flex min-h-screen flex-col bg-slate-950 text-slate-100" data-testid="library-page">
        <PapersPage onOpenReview={id => setOpened(id)} />
        <output data-testid="opened-review">{opened ?? ''}</output>
      </div>
    </FileSearchConfirmationProvider>
  )
}

export function KnowledgeCategoryEditorHarness() {
  const [open, setOpen] = useState(false)
  return (
    <div className="flex min-h-screen flex-col bg-slate-950 text-slate-100" data-testid="knowledge-category-editor-harness">
      <header data-testid="knowledge-title-bar" className="flex items-center gap-3 border-b border-slate-800 px-6 py-2.5">
        <h1 className="text-base font-semibold">知识图谱</h1>
        <button type="button" className="ml-auto rounded-lg bg-teal-500 px-2.5 py-1.5 text-xs text-white">新增概念</button>
      </header>
      <div data-testid="knowledge-filter-bar" className="flex flex-wrap items-center gap-2 border-b border-slate-800 px-6 py-1.5">
        <button type="button" className="rounded-lg bg-slate-800 px-3 py-1.5 text-xs">节点图谱</button>
        <button type="button" className="rounded-lg px-3 py-1.5 text-xs text-slate-500">编译图谱</button>
        <button type="button" className="rounded-lg px-3 py-1.5 text-xs text-slate-500">概念</button>
        <span className="text-[11.5px] text-slate-400">全部 1064</span>
        <span className="text-[11.5px] text-slate-400">论文 200</span>
        <span className="text-[11.5px] text-slate-400">技术 491</span>
        <span className="text-[11.5px] text-slate-400">数据集 369</span>
        <KnowledgePaperCategoryEditorButton onClick={() => setOpen(true)} />
      </div>
      {open && <CategoryComposer onClose={() => setOpen(false)} />}
    </div>
  )
}
