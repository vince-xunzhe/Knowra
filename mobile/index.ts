import { registerRootComponent } from 'expo'

// ── Diagnostic harness ────────────────────────────────────────────────
// Release/TestFlight builds hide React Native's red error box, so an
// uncaught JS error becomes a black screen or a silent native abort. The
// <RootErrorBoundary> in App only catches *render* errors; this file also
// catches:
//   1. async / runtime uncaught errors  → global handler shows an Alert
//   2. module-init errors (App's import tree throwing) → try/catch fallback
// so failures are visible on a real device instead of going dark.

// 1) Global handler — set BEFORE loading App so it's active during init.
const g = global as unknown as {
  ErrorUtils?: {
    getGlobalHandler?: () => ((e: unknown, fatal?: boolean) => void) | undefined
    setGlobalHandler?: (fn: (e: unknown, fatal?: boolean) => void) => void
  }
}
const prevHandler = g.ErrorUtils?.getGlobalHandler?.()
g.ErrorUtils?.setGlobalHandler?.((error: unknown, isFatal?: boolean) => {
  const e = error as { name?: string; message?: string; stack?: string }
  try {
    // Lazy-require so this file has no top-level RN import that could fail.
    const { Alert } = require('react-native')
    Alert.alert(
      `运行错误${isFatal ? '（fatal）' : ''}`,
      `${e?.name || 'Error'}: ${e?.message || String(error)}\n\n${String(e?.stack || '').slice(0, 800)}`,
    )
  } catch {
    /* Alert unavailable — nothing more we can do */
  }
  // During diagnosis, don't re-crash on fatal (that would hide the Alert).
  if (!isFatal && prevHandler) prevHandler(error, isFatal)
})

// Minimal on-screen error component for a module-init failure.
function makeErrorApp(message: string) {
  const React = require('react')
  const { View, Text, ScrollView } = require('react-native')
  return function ErrorApp() {
    return React.createElement(
      View,
      { style: { flex: 1, backgroundColor: '#0b0d12', paddingTop: 80, paddingHorizontal: 20 } },
      React.createElement(
        Text,
        { style: { color: '#fda4af', fontSize: 18, fontWeight: '700', marginBottom: 12 } },
        '启动失败（模块加载）',
      ),
      React.createElement(
        ScrollView,
        null,
        React.createElement(
          Text,
          { style: { color: '#fca5a5', fontSize: 12, lineHeight: 18 } },
          message,
        ),
      ),
    )
  }
}

// 2) Load App with a guard around its (synchronous) module-init.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let App: any
try {
  App = require('./App').default
} catch (err) {
  const e = err as { message?: string; stack?: string }
  App = makeErrorApp(`${e?.message || String(err)}\n\n${String(e?.stack || '').slice(0, 1200)}`)
}

registerRootComponent(App)
