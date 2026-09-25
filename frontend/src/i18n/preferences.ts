import { useSyncExternalStore } from 'react'
import { getLocale, getTheme, subscribe } from './store'
export function useLocale() { return useSyncExternalStore(subscribe, getLocale) }
export function useTheme() { return useSyncExternalStore(subscribe, getTheme) }
