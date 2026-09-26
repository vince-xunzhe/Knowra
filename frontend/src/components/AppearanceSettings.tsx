import { useState } from 'react'
import { isAxiosError } from 'axios'
import { ChevronDown, Globe2, Sun } from 'lucide-react'
import { savePresentationLanguage } from '../api/client'
import { t } from '../i18n/catalog'
import { useLocale, useTheme } from '../i18n/preferences'
import { LANGUAGES, setLocale, setTheme, type Locale, type Theme } from '../i18n/store'

export default function AppearanceSettings() {
  const locale = useLocale()
  const theme = useTheme()
  const [pending, setPending] = useState<Locale | null>(null)
  const [error, setError] = useState<'restart' | 'connection' | null>(null)
  async function changeLanguage(next: Locale) {
    if (next === locale) return
    setPending(next)
    setError(null)
    try {
      await savePresentationLanguage(next)
      setLocale(next)
    } catch (cause) {
      setError(isAxiosError(cause) && cause.response?.status === 404 ? 'restart' : 'connection')
    }
    finally { setPending(null) }
  }
  return (
    <section className="mb-6 rounded-2xl border border-slate-800 bg-[var(--surface-0f1117)] p-5 sm:p-6" aria-labelledby="appearance-title">
      <div className="mb-6">
        <p className="text-[10px] font-semibold tracking-[0.16em] uppercase text-indigo-300">{t('个性化')}</p>
        <h2 id="appearance-title" className="mt-1 text-lg font-semibold text-foreground">{t('语言与外观')}</h2>
        <p className="mt-1 text-sm text-slate-400">{t('让 Knowra 更适合你的阅读习惯。更改后即时生效并自动保存。')}</p>
      </div>
      <div className="space-y-5">
        <div>
          <label htmlFor="appearance-language" className="mb-2 flex items-center gap-2 text-sm font-medium text-foreground"><Globe2 size={16} />{t('显示语言')}</label>
          <div className="relative w-full max-w-sm">
            <select id="appearance-language" value={pending ?? locale} disabled={pending !== null} onChange={event => void changeLanguage(event.target.value as Locale)} aria-describedby="appearance-language-hint appearance-language-status" className="w-full appearance-none rounded-xl border border-slate-700 bg-[var(--surface-0b0d12)] py-3 pl-4 pr-10 text-sm text-foreground outline-none transition-colors hover:border-slate-500 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20 disabled:cursor-wait disabled:opacity-60">
              {LANGUAGES.map(language => <option key={language.id} value={language.id} lang={language.lang}>{language.label}</option>)}
            </select>
            <ChevronDown size={16} aria-hidden="true" className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" />
          </div>
          <p id="appearance-language-hint" className="mt-2 text-xs leading-relaxed text-slate-400">{t('界面与新任务的提示词按语言加载。每种语言的修改独立保存，已有内容保持原样。')}</p>
          <p id="appearance-language-status" aria-live="polite" className={`text-xs ${pending || error ? 'mt-2' : ''} ${error ? 'text-rose-300' : 'text-slate-400'}`}>{pending ? t('切换语言中…') : error === 'restart' ? t('当前后端尚未加载语言设置接口，请重启本地服务后重试。') : error ? t('语言保存失败，请检查后端连接后重试。') : ''}</p>
        </div>
        <div className="border-t border-slate-800 pt-5">
          <label htmlFor="appearance-theme" className="mb-2 flex items-center gap-2 text-sm font-medium text-foreground"><Sun size={16} />{t('主题配色')}</label>
          <div className="relative w-full max-w-sm">
            <select id="appearance-theme" value={theme} onChange={event => setTheme(event.target.value as Theme)} aria-describedby="appearance-theme-hint" className="w-full appearance-none rounded-xl border border-slate-700 bg-[var(--surface-0b0d12)] py-3 pl-4 pr-10 text-sm text-foreground outline-none transition-colors hover:border-slate-500 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500/20">
              <option value="dark">Dark</option>
              <option value="light">Light</option>
            </select>
            <ChevronDown size={16} aria-hidden="true" className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" />
          </div>
          <p id="appearance-theme-hint" className="mt-2 text-xs leading-relaxed text-slate-400">{t('仅调整界面配色，主题偏好保存在当前设备。')}</p>
        </div>
      </div>
    </section>
  )
}
