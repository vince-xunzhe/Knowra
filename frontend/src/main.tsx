import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import 'katex/dist/katex.min.css'
import App from './App.tsx'
import { getPresentationPreferences } from './api/client'
import { setLocale } from './i18n/store'
import { CloudAuthProvider } from './hooks/useCloudAuth'

void getPresentationPreferences().then(({ locale }) => setLocale(locale)).catch(() => { /* Keep cached preferences offline. */ })

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <CloudAuthProvider>
      <App />
    </CloudAuthProvider>
  </StrictMode>,
)
