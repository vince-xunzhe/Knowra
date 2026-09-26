export const LANGUAGES = [
  { id: 'zh', label: '中文', lang: 'zh-CN' },
  { id: 'ja', label: '日本語', lang: 'ja' },
  { id: 'es', label: 'Español', lang: 'es' },
  { id: 'en', label: 'English', lang: 'en' },
] as const
export type Locale = typeof LANGUAGES[number]['id']
export type Theme = 'dark' | 'light'
const listeners = new Set<() => void>()
function read(key: string) { try { return localStorage.getItem(key) } catch { return null } }
export function isLocale(value: unknown): value is Locale { return LANGUAGES.some(l => l.id === value) }
let locale: Locale = isLocale(read('knowra.locale')) ? read('knowra.locale') as Locale : 'zh'
let theme: Theme = read('knowra.theme') === 'light' ? 'light' : 'dark'
export const getLocale = () => locale
export const getTheme = () => theme
export const getFormattingLocale = () => LANGUAGES.find(l => l.id === locale)!.lang
export function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener) } }
function apply() {
  document.documentElement.lang = getFormattingLocale()
  document.documentElement.dataset.theme = theme
  document.documentElement.style.colorScheme = theme
}
function persist(key: string, value: string) { try { localStorage.setItem(key, value) } catch { /* Session preferences still work in restricted browsers. */ } }
export function setLocale(value: Locale) { if (!isLocale(value)) return; locale = value; persist('knowra.locale', value); apply(); listeners.forEach(fn => fn()) }
export function setTheme(value: Theme) { theme = value; persist('knowra.theme', value); apply(); listeners.forEach(fn => fn()) }
apply()
window.addEventListener('storage', event => {
  if (event.key === 'knowra.locale' && isLocale(event.newValue)) locale = event.newValue
  if (event.key === 'knowra.theme' && (event.newValue === 'dark' || event.newValue === 'light')) theme = event.newValue
  apply(); listeners.forEach(fn => fn())
})
