import { useState } from 'react'
import { Network, BookOpen, FileText, BookMarked, Pencil, Settings } from 'lucide-react'
import GraphPage from './pages/GraphPage'
import PapersPage from './pages/PapersPage'
import ReviewPage from './pages/ReviewPage'
import ConceptsPage from './pages/ConceptsPage'
import PromptEditorPage from './pages/PromptEditorPage'
import SettingsPage from './pages/SettingsPage'
import ProcessingStatus from './components/ProcessingStatus'
import WikiCompileStatus from './components/WikiCompileStatus'

type Page = 'graph' | 'papers' | 'review' | 'concepts' | 'prompt' | 'settings'
type NavItem =
  | { id: Page; icon: typeof Network; label: string }
  | { divider: true }

const NAV: NavItem[] = [
  { id: 'graph', icon: Network, label: '图谱' },
  { id: 'papers', icon: BookOpen, label: '论文' },
  { id: 'review', icon: FileText, label: '回顾' },
  { id: 'concepts', icon: BookMarked, label: 'Wiki' },
  { divider: true },
  { id: 'prompt', icon: Pencil, label: 'Prompt' },
  { id: 'settings', icon: Settings, label: '设置' },
]

export default function App() {
  const [page, setPage] = useState<Page>('graph')

  return (
    <div className="flex h-screen overflow-hidden bg-[#0b0d12] text-slate-200">
      {/* Sidebar */}
      <nav className="w-[4.75rem] bg-[#0f1117] border-r border-slate-800/80 flex flex-col items-center py-4 gap-1 shrink-0">
        <div className="w-10 h-10 bg-gradient-to-br from-indigo-500 to-violet-600 rounded-2xl flex items-center justify-center mb-4 shadow-lg shadow-indigo-500/20">
          <Network size={18} className="text-white" />
        </div>
        {NAV.map((item, idx) => {
          if ('divider' in item) {
            return <div key={`d-${idx}`} className="w-7 h-px bg-slate-800/80 my-2" />
          }
          const { id, icon: Icon, label } = item
          const active = page === id
          return (
            <button
              key={id}
              onClick={() => setPage(id)}
              title={label}
              className={`relative group w-14 h-14 px-1 rounded-2xl flex flex-col items-center justify-center gap-1 transition-all ${
                active
                  ? 'bg-indigo-500/15 text-indigo-100 shadow-inner shadow-indigo-500/10'
                  : 'text-slate-600 hover:text-slate-100 hover:bg-slate-800/50'
              }`}
            >
              <Icon size={18} strokeWidth={active ? 2.2 : 1.7} />
              <span
                className={`text-[10.5px] leading-none tracking-[0.02em] truncate max-w-full ${
                  active ? 'text-indigo-100' : 'text-slate-600 group-hover:text-slate-300'
                }`}
              >
                {label}
              </span>
              {active && (
                <span className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-6 rounded-r bg-indigo-400" />
              )}
            </button>
          )
        })}
      </nav>

      {/* Main */}
      <main className="flex-1 min-w-0 overflow-hidden">
        {page === 'graph' && <GraphPage />}
        {page === 'papers' && <PapersPage />}
        {page === 'review' && <ReviewPage />}
        {page === 'concepts' && <ConceptsPage />}
        {page === 'prompt' && <PromptEditorPage />}
        {page === 'settings' && <SettingsPage />}
      </main>

      <ProcessingStatus />
      <WikiCompileStatus />
    </div>
  )
}
