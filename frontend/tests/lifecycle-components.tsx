import { useState } from 'react'
import AskDrawer from '../src/components/AskDrawer'
import RejectedRescueModal from '../src/components/RejectedRescueModal'
import BackgroundTasks from '../src/components/BackgroundTasks'
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
