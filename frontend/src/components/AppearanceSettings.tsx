import { useState } from 'react'
import { Check, Globe2, Moon, Sun } from 'lucide-react'
import { savePresentationLanguage } from '../api/client'
import { t } from '../i18n/catalog'
import { useLocale, useTheme } from '../i18n/preferences'
import { LANGUAGES, setLocale, setTheme, type Locale } from '../i18n/store'

export default function AppearanceSettings() {
  const locale = useLocale()
  const theme = useTheme()
  const [pending, setPending] = useState<Locale | null>(null)
  const [error, setError] = useState(false)
  async function changeLanguage(next: Locale) {
    if (next === locale) return
    setPending(next)
    setError(false)
    try {
      await savePresentationLanguage(next)
      setLocale(next)
    } catch { setError(true) }
    finally { setPending(null) }
  }
  return (
    <section className="mb-6 rounded-2xl border border-slate-800 bg-[var(--surface-0f1117)] p-5 sm:p-6" aria-labelledby="appearance-title">
      <div className="mb-6">
        <p className="text-[10px] font-semibold tracking-[0.16em] uppercase text-indigo-300">{t('个性化')}</p>
        <h2 id="appearance-title" className="mt-1 text-lg font-semibold text-foreground">{t('语言与外观')}</h2>
        <p className="mt-1 text-sm text-slate-400">{t('让 Knowra 更适合你的阅读习惯。更改后即时生效并自动保存。')}</p>
      </div>
      <div className="grid gap-8 lg:grid-cols-2">
        <fieldset disabled={pending !== null} className="min-w-0">
          <legend className="mb-3 flex items-center gap-2 text-sm font-medium text-foreground"><Globe2 size={16} />{t('显示语言')}</legend>
          <div className="grid grid-cols-2 gap-2">
            {LANGUAGES.map(language => (
              <label key={language.id} className={`relative flex cursor-pointer items-center justify-between gap-2 rounded-xl border px-4 py-3 text-sm transition-colors ${(pending ?? locale) === language.id ? 'border-indigo-500 bg-indigo-500/10 text-indigo-200' : 'border-slate-800 text-slate-300 hover:border-slate-600'} ${pending ? 'opacity-60' : ''}`}>
                <input type="radio" name="language" value={language.id} checked={(pending ?? locale) === language.id} onChange={() => void changeLanguage(language.id)} className="peer absolute inset-0 z-10 h-full w-full cursor-pointer opacity-0" />
                <span lang={language.lang}>{language.label}</span>
                {(pending ?? locale) === language.id && <Check size={16} aria-hidden="true" />}
                <span className="pointer-events-none absolute inset-0 rounded-xl peer-focus-visible:ring-2 peer-focus-visible:ring-indigo-400 peer-focus-visible:ring-offset-2" />
              </label>
            ))}
          </div>
          <p className="mt-3 text-xs leading-relaxed text-slate-400">{t('界面与新任务的提示词按语言加载。每种语言的修改独立保存，已有内容保持原样。')}</p>
          <p aria-live="polite" className={`mt-2 text-xs ${error ? 'text-rose-300' : 'text-slate-400'}`}>{pending ? t('切换语言中…') : error ? t('语言保存失败，请检查后端连接后重试。') : ''}</p>
        </fieldset>
        <fieldset className="min-w-0">
          <legend className="mb-3 flex items-center gap-2 text-sm font-medium text-foreground"><Sun size={16} />{t('主题配色')}</legend>
          <div className="grid grid-cols-2 gap-3">
            {(['dark', 'light'] as const).map(option => (
              <label key={option} className={`relative cursor-pointer rounded-xl border p-2 transition-colors ${theme === option ? 'border-indigo-500 bg-indigo-500/10' : 'border-slate-800 hover:border-slate-600'}`}>
                <input type="radio" name="theme" value={option} checked={theme === option} onChange={() => setTheme(option)} className="peer absolute inset-0 z-10 h-full w-full cursor-pointer opacity-0" />
                <div className={`theme-preview theme-preview-${option}`} aria-hidden="true">
                  <div className="theme-preview-nav"><i /><i /><i /></div>
                  <div className="theme-preview-content"><b /><div><i /><i /></div><em /></div>
                </div>
                <span className="flex items-center gap-2 px-1 pt-2 pb-1 text-sm text-foreground">{option === 'dark' ? <Moon size={14} /> : <Sun size={14} />}{option === 'dark' ? 'Dark' : 'Light'}{theme === option && <Check size={14} className="ml-auto text-indigo-300" />}</span>
                <span className="pointer-events-none absolute inset-0 rounded-xl peer-focus-visible:ring-2 peer-focus-visible:ring-indigo-400 peer-focus-visible:ring-offset-2" />
              </label>
            ))}
          </div>
          <p className="mt-3 text-xs leading-relaxed text-slate-400">{t('仅调整界面配色，主题偏好保存在当前设备。')}</p>
        </fieldset>
      </div>
    </section>
  )
}
