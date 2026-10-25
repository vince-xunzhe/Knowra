// Test entry point: render the production components without App/auth/backend startup.
// The browser runner supplies synthetic storage and intercepts every API request.
import { createRoot } from 'react-dom/client'
import ReviewPage from '../src/pages/ReviewPage'
import { AskHarness, RescueHarness } from './lifecycle-components'
import '../src/index.css'

const target = new URLSearchParams(location.search).get('component')
createRoot(document.getElementById('root')!).render(
  target === 'review' ? <ReviewPage initialPaperId={101} /> : target === 'rescue' ? <RescueHarness /> : <AskHarness />,
)
