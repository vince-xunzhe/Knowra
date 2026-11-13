import { createRoot } from 'react-dom/client'
import ReviewPage from '../src/pages/ReviewPage'
import { FileSearchConfirmationProvider } from '../src/hooks/useFileSearchConfirmation'
import { AskHarness, RescueHarness, BackgroundTasksHarness, LibraryPapersHarness } from './lifecycle-components'
import '../src/index.css'

const target = new URLSearchParams(location.search).get('component')
createRoot(document.getElementById('root')!).render(
  target === 'review' ? (
    <FileSearchConfirmationProvider>
      <ReviewPage initialPaperId={101} />
    </FileSearchConfirmationProvider>
  ) : target === 'tasks' ? (
    <BackgroundTasksHarness />
  ) : target === 'library' ? (
    <LibraryPapersHarness />
  ) : target === 'rescue' ? <RescueHarness /> : <AskHarness />,
)
