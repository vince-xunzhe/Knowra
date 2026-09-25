import en from './locales/en.json'
import ja from './locales/ja.json'
import es from './locales/es.json'
import { getLocale, type Locale } from './store'
const catalogs: Record<string, Record<string, string>> = { en, ja, es }
/** Only developer-owned UI messages go here. Paper text, names and user input stay untouched. */
export function t(source: string, params: Record<string, unknown> = {}, locale: Locale = getLocale()): string {
  const message = catalogs[locale]?.[source] ?? source
  return message.replace(/\{(\w+)\}/g, (match, key: string) => key in params ? String(params[key]) : match)
}

/** Recognize legacy notices/session defaults without using translated labels as identifiers. */
export function matchesMessage(value: string | undefined, source: string): boolean {
  return value === source || Object.values(catalogs).some(catalog => catalog[source] === value)
}
