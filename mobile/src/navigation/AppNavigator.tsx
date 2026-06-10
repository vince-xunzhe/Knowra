/**
 * Top-level navigator. Two layers:
 *
 *   1. AuthGate — picks LoginScreen vs the tabbed app based on
 *      `useAuth().user`. Renders a splash while AsyncStorage hydrates.
 *   2. Tabs (资料 / Ask / 设置). The 资料 tab is a native-stack so the
 *      paper-detail screen pushes neatly without leaving the tab.
 */
import React from 'react'
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native'
import { NavigationContainer, DefaultTheme } from '@react-navigation/native'
import { createNativeStackNavigator } from '@react-navigation/native-stack'
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs'
import { Ionicons } from '@expo/vector-icons'

import { useAuth } from '../contexts/AuthContext'
import LoginScreen from '../screens/LoginScreen'
import PapersScreen from '../screens/PapersScreen'
import PaperDetailScreen from '../screens/PaperDetailScreen'
import AskScreen from '../screens/AskScreen'
import SettingsScreen from '../screens/SettingsScreen'
import type { RootStackParamList } from './types'

// Ionicons glyph per tab. Outline when inactive, solid when focused —
// the standard iOS tab-bar idiom, replaces the default placeholder
// triangle markers.
type IoniconName = React.ComponentProps<typeof Ionicons>['name']
const TAB_ICONS: Record<string, { on: IoniconName; off: IoniconName }> = {
  资料: { on: 'document-text', off: 'document-text-outline' },
  Ask: { on: 'chatbubbles', off: 'chatbubbles-outline' },
  设置: { on: 'settings', off: 'settings-outline' },
  登录: { on: 'log-in', off: 'log-in-outline' },
}

function tabIcon(routeName: string) {
  return ({ focused, color, size }: { focused: boolean; color: string; size: number }) => {
    const pair = TAB_ICONS[routeName]
    if (!pair) return null
    return <Ionicons name={focused ? pair.on : pair.off} size={size} color={color} />
  }
}

const Tabs = createBottomTabNavigator()
const PapersStack = createNativeStackNavigator<RootStackParamList>()

const knowraTheme = {
  ...DefaultTheme,
  dark: true,
  colors: {
    ...DefaultTheme.colors,
    background: '#0b0d12',
    card: '#0f1117',
    text: '#e2e8f0',
    border: '#1e293b',
    primary: '#6366f1',
    notification: '#f43f5e',
  },
}

const screenOptions = {
  headerStyle: { backgroundColor: '#0f1117' },
  headerTintColor: '#e2e8f0',
  headerTitleStyle: { fontWeight: '600' as const },
}

function PapersTab() {
  return (
    <PapersStack.Navigator screenOptions={screenOptions}>
      <PapersStack.Screen name="PapersList" component={PapersScreen} options={{ title: '资料' }} />
      <PapersStack.Screen
        name="PaperDetail"
        component={PaperDetailScreen}
        options={({ route }) => ({ title: route.params.title, headerBackTitle: '资料' })}
      />
    </PapersStack.Navigator>
  )
}

function MainTabs() {
  return (
    <Tabs.Navigator
      screenOptions={({ route }) => ({
        headerShown: false,
        tabBarStyle: {
          backgroundColor: '#0f1117',
          borderTopColor: '#1e293b',
          // A touch taller so the icon + label breathe.
          paddingTop: 4,
        },
        tabBarActiveTintColor: '#a5b4fc',
        tabBarInactiveTintColor: '#64748b',
        tabBarLabelStyle: { fontSize: 11, fontWeight: '500' },
        tabBarIcon: tabIcon(route.name),
      })}
    >
      <Tabs.Screen name="资料" component={PapersTab} />
      <Tabs.Screen name="Ask" component={AskScreen}
        options={{
          headerShown: true, ...screenOptions, title: '提问',
        }} />
      <Tabs.Screen name="设置" component={SettingsScreen}
        options={{
          headerShown: true, ...screenOptions, title: '设置',
        }} />
    </Tabs.Navigator>
  )
}

export default function AppNavigator() {
  const auth = useAuth()

  if (auth.loading) {
    return (
      <View style={styles.splash}>
        <ActivityIndicator color="#818cf8" size="large" />
        <Text style={styles.splashText}>启动中…</Text>
      </View>
    )
  }

  return (
    <NavigationContainer theme={knowraTheme}>
      {auth.user ? <MainTabs /> : <LoginOrSettingsStack />}
    </NavigationContainer>
  )
}

/**
 * Pre-login flow. The user might land here with EMPTY config — they
 * need to enter Supabase URL etc. first. So we show a 2-tab stack:
 * login is the default, but settings is reachable so they can fill in
 * the connection params before trying to log in.
 */
function LoginOrSettingsStack() {
  return (
    <Tabs.Navigator
      screenOptions={({ route }) => ({
        headerShown: false,
        tabBarStyle: { backgroundColor: '#0f1117', borderTopColor: '#1e293b', paddingTop: 4 },
        tabBarActiveTintColor: '#a5b4fc',
        tabBarInactiveTintColor: '#64748b',
        tabBarLabelStyle: { fontSize: 11, fontWeight: '500' },
        tabBarIcon: tabIcon(route.name),
      })}
    >
      <Tabs.Screen name="登录" component={LoginScreen} />
      <Tabs.Screen
        name="设置"
        component={SettingsScreen}
        options={{ headerShown: true, ...screenOptions, title: '云端连接' }}
      />
    </Tabs.Navigator>
  )
}

const styles = StyleSheet.create({
  splash: { flex: 1, backgroundColor: '#0b0d12', alignItems: 'center', justifyContent: 'center' },
  splashText: { color: '#64748b', marginTop: 14, fontSize: 13 },
})
