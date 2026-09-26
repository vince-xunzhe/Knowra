import { t as tr } from './i18n/catalog'
import { useLocale } from './i18n/preferences'
import { useState } from 'react'
import { Network, BookOpen, FileText, Settings, BarChart3, Sparkles } from 'lucide-react'
import GraphPage from './pages/GraphPage'
import PapersPage from './pages/PapersPage'
import ReviewPage from './pages/ReviewPage'
import RecommendPage from './pages/RecommendPage'
import SettingsPage from './pages/SettingsPage'
import DashboardPage from './pages/DashboardPage'
import ProcessingStatus from './components/ProcessingStatus'
import WikiCompileStatus from './components/WikiCompileStatus'
import WikiLintStatus from './components/WikiLintStatus'
import { WikiLintProvider } from './hooks/useWikiLint'
import knowraLogo from './assets/knowra-logo.jpeg'

type Page = 'graph' | 'papers' | 'review' | 'recommend' | 'dashboard' | 'settings'
type NavItem =
  | { id: Page; icon: typeof Network; label: string }
  | { divider: true }

// Prompt is intentionally not in this sidebar — it's a per-extraction config
// that only matters in the context of materials processing, so it lives
// behind a collapsible column on the 资料 page. The Wiki layer (paper /
// concept .md pages) is folded into the Graph page: the left
// PipelineConsole exposes the ingest / curate / compile / maintain workflow
// as 4 stage cards, the node drawer renders compiled .md inline, and a
// view toggle flips to the compiled-graph swim-lane. Concept promotion
// review also lives in the Graph page (inside stage ② of the console +
// drawer + rescue modal) so curation happens in visual context instead of
// as flat lists.
//
// Nav order goes from "high-altitude knowledge" → "narrative reading" →
// "raw materials" → "observability" → "config" so the user passes the
// finished artifact (graph) and synthesis (review) before the raw
// inventory (materials).
const NAV: NavItem[] = [
  { id: 'graph', icon: Network, get label() { return tr("知识") } },
  { id: 'review', icon: FileText, get label() { return tr("回顾") } },
  { id: 'recommend', icon: Sparkles, get label() { return tr("推荐") } },
  { id: 'papers', icon: BookOpen, get label() { return tr("资料") } },
  { id: 'dashboard', icon: BarChart3, get label() { return tr("看板") } },
  { divider: true },
  { id: 'settings', icon: Settings, get label() { return tr("设置") } },
]

export default function App() {
  useLocale()
  return <WikiLintProvider><AppContent /></WikiLintProvider>
}

function AppContent() {
  useLocale()
  const [page, setPage] = useState<Page>('graph')
  const [lintOpen, setLintOpen] = useState(false)
  const [reviewPaperId, setReviewPaperId] = useState<number | null>(null)

  const openPage = (nextPage: Page) => {
    if (nextPage === 'review') setReviewPaperId(null)
    setPage(nextPage)
  }

  const openPaperReview = (paperId: number) => {
    setReviewPaperId(paperId)
    setPage('review')
  }

  return (
    <div className="flex h-screen overflow-hidden bg-[var(--surface-0b0d12)] text-slate-200">
      {/* Sidebar */}
      <nav className="w-[6.5rem] bg-[var(--surface-0f1117)] border-r border-slate-800/80 flex flex-col items-center py-4 gap-1 shrink-0">
        <img src={knowraLogo} alt="Knowra" width={40} height={40} className="mb-4 h-10 w-10 shrink-0 rounded-xl bg-white object-contain" />
        {NAV.map((item, idx) => {
          if ('divider' in item) {
            return <div key={`d-${idx}`} className="w-7 h-px bg-slate-800/80 my-2" />
          }
          const { id, icon: Icon, label } = item
          const active = page === id
          return (
            <button
              key={id}
              onClick={() => openPage(id)}
              title={label}
              className={`relative group w-[5.75rem] h-14 px-1 rounded-2xl flex flex-col items-center justify-center gap-1 transition-all ${
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
        {page === 'graph' && <GraphPage lintOpen={lintOpen} setLintOpen={setLintOpen} />}
        {page === 'papers' && <PapersPage onOpenReview={openPaperReview} />}
        {page === 'review' && <ReviewPage initialPaperId={reviewPaperId} />}
        {page === 'recommend' && <RecommendPage />}
        {page === 'dashboard' && <DashboardPage />}
        {page === 'settings' && <SettingsPage />}
      </main>

      <ProcessingStatus />
      <WikiCompileStatus />
      <WikiLintStatus onOpen={() => { setPage('graph'); setLintOpen(true) }} />
    </div>
  )
}
