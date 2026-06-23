import { registerRootComponent } from 'expo'

import App from './App'

// registerRootComponent calls AppRegistry.registerComponent('main', () => App)
// and sets up the environment appropriately for Expo (native + web).
// (Render-time errors are still surfaced on-device by <RootErrorBoundary> in App.)
registerRootComponent(App)
