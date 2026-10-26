// Production editors against the disposable fixture's real production API routers.
import { createRoot } from 'react-dom/client'
import DomainPage from '../src/pages/DomainPage'
import '../src/index.css'

const mode = new URLSearchParams(location.search).get('mode') === 'roadmap' ? 'roadmap' : 'domain'
createRoot(document.getElementById('root')!).render(
  <DomainPage mode={mode} active onOpenReview={() => undefined} />,
)
