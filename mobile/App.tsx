import 'react-native-gesture-handler'
import React from 'react'
import { StatusBar } from 'expo-status-bar'
import { ScrollView, StyleSheet, Text, View } from 'react-native'
import { GestureHandlerRootView } from 'react-native-gesture-handler'
import { SafeAreaProvider } from 'react-native-safe-area-context'

import { AuthProvider } from './src/contexts/AuthContext'
import { SnapshotProvider } from './src/contexts/SnapshotContext'
import AppNavigator from './src/navigation/AppNavigator'

// Production (release) builds don't show React Native's red error overlay, so
// an unhandled error during render silently becomes a black screen. This
// boundary surfaces the message + stack on-screen instead, so failures are
// diagnosable on a real device / TestFlight build.
class RootErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: unknown) {
    // eslint-disable-next-line no-console
    console.error('[Knowra] root render error:', error, info)
  }

  render() {
    if (this.state.error) {
      return (
        <View style={styles.errRoot}>
          <Text style={styles.errTitle}>应用启动出错</Text>
          <ScrollView style={styles.errScroll}>
            <Text style={styles.errMsg}>{String(this.state.error?.message || this.state.error)}</Text>
            {!!this.state.error?.stack && (
              <Text style={styles.errStack}>{this.state.error.stack}</Text>
            )}
          </ScrollView>
        </View>
      )
    }
    return this.props.children
  }
}

export default function App() {
  return (
    // GestureHandlerRootView is REQUIRED at the app root for React Navigation
    // in a standalone build. Expo Go provides one implicitly (hence it worked
    // there), but the TestFlight build does not → screens render black without
    // this wrapper.
    <GestureHandlerRootView style={{ flex: 1 }}>
      <RootErrorBoundary>
        <SafeAreaProvider>
          <AuthProvider>
            <SnapshotProvider>
              <StatusBar style="light" />
              <AppNavigator />
            </SnapshotProvider>
          </AuthProvider>
        </SafeAreaProvider>
      </RootErrorBoundary>
    </GestureHandlerRootView>
  )
}

const styles = StyleSheet.create({
  errRoot: { flex: 1, backgroundColor: '#0b0d12', paddingTop: 80, paddingHorizontal: 20 },
  errTitle: { color: '#fda4af', fontSize: 18, fontWeight: '700', marginBottom: 12 },
  errScroll: { flex: 1 },
  errMsg: { color: '#fca5a5', fontSize: 13, lineHeight: 20, marginBottom: 12 },
  errStack: { color: '#64748b', fontSize: 10, fontFamily: 'Menlo', lineHeight: 15 },
})
